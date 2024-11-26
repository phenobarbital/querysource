import asyncio
from typing import Optional
from aiohttp import web
from ...exceptions import (
    SlugNotFound,
    QueryException,
    DriverError,
    DataNotFound,
    ParserError
)
from importlib import import_module
from ..abstract import BaseQuery
from .transformations import (
    GoogleMaps,
)
from .operators.filter import Filter
from .outputs import TableOutput
from .sources import ThreadQuery, ThreadFile


def get_operator_module(clsname: str):
    """
    Get an Operator Module
    """
    try:
        clsobj = import_module(
            f'.operators.{clsname}',
            package=__package__
        )
        return getattr(clsobj, clsname)
    except ImportError as exc:
        raise ImportError(
            f"Error importing an Operator {clsname}: {exc}"
        ) from exc


def get_transform_module(clsname: str):
    """
    Get a Transformation Module
    """
    try:
        clsobj = import_module(
            f'.transformations.{clsname}',
            package=__package__
        )
        return getattr(clsobj, clsname)
    except ImportError as exc:
        raise ImportError(
            f"Error importing {clsname}: {exc}"
        ) from exc


class MultiQS(BaseQuery):
    """
    MultiQS.

       Query multiple data-origins or files in QuerySource.
    """
    def __init__(
            self,
            slug: str = None,
            queries: Optional[list] = None,
            files: Optional[list] = None,
            query: Optional[dict] = None,
            conditions: dict = None,
            request: web.Request = None,
            loop: asyncio.AbstractEventLoop = None,
            **kwargs
    ):
        super(MultiQS, self).__init__(
            slug=slug,
            conditions=conditions,
            request=request,
            loop=loop,
            **kwargs
        )
        # creates the Result Queue:
        self._queue = asyncio.Queue()
        if self.slug is not None:
            # extracting JSON from the Slug Table:
            self._type = 'slug'
        # queries and files:
        self._queries = queries
        self._files = files
        # Query Options:
        self._options: dict = query
        if query:
            ## Getting data from Queries or Files
            self._queries = query.get('queries', {})
            self._files = query.get('files', {})
        if not (self.slug or self._queries or self._files):
            # Check if both are effectively empty
            raise DriverError(
                (
                    'Invalid Options passed to MultiQuery. '
                    'Slug, Queries and Files are all empty.'
                )
            )

    async def query(self):
        """
        Executing Multiple Queries/Files
        """
        tasks = {}
        if self.slug:
            try:
                query = await self.get_slug(slug=self.slug)
                try:
                    slug_data = self._encoder.load(query.query_raw)
                    if slug_data:
                        self._options = slug_data
                        self._queries = slug_data.get('queries', {})
                        self._files = slug_data.get('files', {})
                        # TODO: making replacements based on POST data.
                except Exception as exc:
                    self.logger.error(
                        f"Unable to decode JSON from Slug {self.slug}: {exc}"
                    )
                    raise DriverError(
                        f"Unable to decode JSON from Slug {self.slug}: {exc}"
                    ) from exc
            except Exception:
                raise
        if self._queries:
            for name, query in self._queries.items():
                print('NAME > ', name, query, 'CONDITIONS > ', self._conditions)
                conditions = self._conditions.pop(name, {})
                # those conditions be applied to the query
                query = {**conditions, **query}
                try:
                    t = ThreadQuery(
                        name, query, self._request, self._queue
                    )
                except Exception as ex:
                    raise self.Error(
                        message=f"Error Starting Query {name}: {ex}",
                        exception=ex
                    ) from ex
                t.start()
                tasks[name] = t
        if self._files:
            for name, file in self._files.items():
                t = ThreadFile(
                    name, file, self._request, self._queue
                )
                t.start()
                tasks[name] = t

        ## then, run all jobs:
        for _, t in tasks.items():
            t.join()
            if t.exc:
                print('EXCEPTION ', t.exc, type(t.exc))
                ## raise exception for this Query
                if isinstance(t.exc, ParserError):
                    raise self.Error(
                        f"Error parsing Query Slug {t.slug()}",
                        exception=t.exc
                    )
                if isinstance(t.exc, SlugNotFound):
                    raise SlugNotFound(
                        f"Slug Not Found: {t.slug}"
                    )
                if isinstance(t.exc, (QueryException, DriverError)):
                    raise self.Error(
                        f"Query Error: {str(t.exc)}",
                        exception=t.exc
                    )
                else:
                    raise self.Error(
                        f"Error on Query: {t!s}",
                        exception=t.exc
                    )
        result = {}
        while not self._queue.empty():
            result.update(await self._queue.get())
        ### Step 2: passing Results to virtual JOINs
        if 'Join' in self._options:
            obj = get_operator_module('Join')
            try:
                ## making Join of Data
                _join = self._options.get('Join', {})
                if isinstance(_join, dict):
                    join = obj(data=result, **_join)
                    result = await join.run()
                elif isinstance(_join, list):
                    for j in _join:
                        join = obj(data=result, **j)
                        result = await join.run()
            except DataNotFound:
                raise
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error making JOIN: {ex!s}",
                    exception=ex
                ) from ex
        elif 'Concat' in self._options:
            obj = get_operator_module('Concat')
            try:
                ## making Join of Data
                concat = obj(data=result, **self._options['Concat'])
                result = await concat.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Concat: {ex!s}",
                    exception=ex
                ) from ex
        elif 'Melt' in self._options:
            try:
                obj = get_operator_module('Melt')
                ## making Join of Data
                melt = obj(data=result, **self._options['Melt'])
                result = await melt.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Melting Data: {ex!s}",
                    exception=ex
                ) from ex
        else:
            # Fallback is to passing one single Dataframe:
            try:
                if len(result.values()) == 1:
                    result = list(result.values())[0]
            except TypeError:
                pass
        ### Step 3: passing result to Transformations
        if 'Transform' in self._options:
            # passing the resultset for several transformation rules.
            for step in self._options['Transform']:
                obj = None
                for step_name, component in step.items():
                    if step_name == 'GoogleMaps':
                        obj = GoogleMaps(data=result, **component)
                        result = await obj.run()
                    else:
                        try:
                            clobj = get_transform_module(step_name)
                            obj = clobj(data=result, **component)
                            result = await obj.run()
                        except ImportError as exc:
                            raise
                        except Exception as ex:
                            raise self.Error(
                                message=f"Error on Transform {step_name}, error: {ex}",
                                exception=ex
                            ) from ex
                continue
        ### Step 4: Check if result is empty or is a dictionary of dataframes:
        if result is None:
            raise self.Error(
                message="Empty Result",
                code=404
            )
        # reduce to one single Dataframe:
        if isinstance(result, dict) and len(result) == 1:
            result = list(result.values())[0]
        ### Step 5: Passing result to any Processor declared
        if 'Processors' in self._options:
            pass
        ### Step 6: Applying Filters to result
        if 'Filter' in self._options:
            try:
                ## making Join of Data
                concat = Filter(data=result, **self._options['Filter'])
                result = await concat.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Filtering: {ex!s}",
                    exception=ex
                ) from ex
        if 'GroupBy' in self._options:
            try:
                obj = get_operator_module('GroupBy')
                ## Group By of Data:
                groupby = obj(data=result, **self._options['GroupBy'])
                result = await groupby.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on GroupBy: {ex!s}",
                    exception=ex
                ) from ex
        ### Step 7: Optionally saving result into Database (using Pandas)
        if 'Output' in self._options:
            for step in self._options['Output']:
                obj = None
                for step_name, component in step.items():
                    if step_name in ('tableOutput', 'TableOutput'):
                        obj = TableOutput(data=result, **component)
                        result = await obj.run()
                    else:
                        # Saving into a DWH selected.
                        pass
        if result is None or len(result) == 0:
            raise DataNotFound(
                "QS Empty Result"
            )
        return result, self._options
