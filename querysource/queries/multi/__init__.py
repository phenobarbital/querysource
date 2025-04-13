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
from ..base import BaseQuery
from .transformations import (
    GoogleMaps,
)
from .operators.filter import Filter
from ...outputs.tables import TableOutput
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
        self._options: dict = query or {}
        # return all dataframes
        self._return_all: bool = kwargs.get('return_all', False)
        if query:
            ## Getting data from Queries or Files
            self._queries = query.pop('queries', {})
            self._files = query.pop('files', {})
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
                    if slug_data := self._encoder.load(query.query_raw):
                        self._options = slug_data
                        self._queries = slug_data.pop('queries', {})
                        self._files = slug_data.pop('files', {})
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
        try:
            for t in tasks.values():
                t.join()
                if t.exc:
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
                    if isinstance(t.exc, DataNotFound):
                        return self.NotFound(
                            message=f"No Data was Found on Query {t.slug()}"
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
        except (QueryException, DriverError) as ex:
            raise
        except Exception as ex:
            raise self.Error(
                message=f"Error on Query: {ex!s}",
                exception=ex
            ) from ex
        while not self._queue.empty():
            result |= await self._queue.get()
        ### Step 2: passing Results to virtual JOINs
        if 'Info' in self._options:
            obj = get_operator_module('Info')
            try:
                ## making Join of Data
                info = obj(data=result)
                async with info as i:
                    result = await i.run()
                return result, self._options
            except DataNotFound:
                raise
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error making Info: {ex!s}",
                    exception=ex
                ) from ex
        if 'Join' in self._options:
            obj = get_operator_module('Join')
            try:
                ## making Join of Data
                _join = self._options.pop('Join', {})
                if isinstance(_join, dict):
                    join = obj(data=result, **_join)
                    async with join as j:
                        result = await j.run()
                elif isinstance(_join, list):
                    for j in _join:
                        join = obj(data=result, **j)
                        async with join as jo:
                            result = await jo.run()
            except DataNotFound:
                raise
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error making JOIN: {ex!s}",
                    exception=ex
                ) from ex
        elif 'Concat' in self._options:
            obj = get_operator_module('Concat')
            _concat = self._options.pop('Concat', {})
            try:
                ## making Join of Data
                concat = obj(data=result, **_concat)
                async with concat as c:
                    result = await c.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Concat: {ex!s}",
                    exception=ex
                ) from ex
        elif 'Melt' in self._options:
            try:
                obj = get_operator_module('Melt')
                _melt = self._options.pop('Melt', {})
                ## making Join of Data
                melt = obj(data=result, **_melt)
                async with melt as mt:
                    result = await mt.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Melting Data: {ex!s}",
                    exception=ex
                ) from ex
        elif 'Merge' in self._options:
            obj = get_operator_module('Merge')
            _merge = self._options.pop('Merge', {})
            try:
                ## making Join of Data
                merge = obj(data=result, **_merge)
                async with merge as m:
                    result = await m.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Merge: {ex!s}",
                    exception=ex
                ) from ex
        else:
            # Fallback is to passing one single Dataframe:
            if self._return_all is False:
                try:
                    if len(result.values()) == 1:
                        result = list(result.values())[0]
                except TypeError:
                    pass
        # Step 3: From Here: iterating over the options:
        _output = self._options.pop('Output', None)
        for step_name, step in self._options.items():
            if step_name == 'Transform':
                for s in step:  # iterating over the list of transformations
                    obj = None
                    for s_name, component in s.items():
                        if s_name == 'GoogleMaps':
                            try:
                                obj = GoogleMaps(data=result, **component)
                                async with obj as google:
                                    result = await google.run()
                            except DataNotFound as ex:
                                raise self.Error(
                                    message="No Data was Found after GoogleMaps)",
                                    exception=ex,
                                    code=404
                                ) from ex
                            except (QueryException, Exception) as ex:
                                raise self.Error(
                                    message=f"Error on GoogleMaps: {ex!s}",
                                    exception=ex
                                ) from ex
                        else:
                            try:
                                clobj = get_transform_module(s_name)
                                obj = clobj(data=result, **component)
                                async with obj as o:
                                    result = await o.run()
                            except ImportError as exc:
                                raise
                            except DataNotFound as ex:
                                raise self.Error(
                                    message=f"No Data was Found after Transform {step_name}.",
                                    exception=ex,
                                    code=404
                                ) from ex
                            except Exception as ex:
                                raise self.Error(
                                    message=f"Error on Transform {step_name}, error: {ex}",
                                    exception=ex
                                ) from ex
                        continue
            if step_name == 'Filter':
                try:
                    ## making Join of Data
                    _filter = Filter(data=result, **step)
                    async with _filter as f:
                        result = await f.run()
                except DataNotFound as ex:
                    raise self.Error(
                        message="No Data was Found after Filtering.",
                        exception=ex,
                        code=404
                    ) from ex
                except (QueryException, Exception) as ex:
                    raise self.Error(
                        message=f"Error on Filtering: {ex!s}",
                        exception=ex
                    ) from ex
            if step_name == 'GroupBy':
                try:
                    obj = get_operator_module('GroupBy')
                    ## Group By of Data:
                    groupby = obj(data=result, **step)
                    async with groupby as g:
                        result = await g.run()
                except DataNotFound as ex:
                    raise self.Error(
                        message="No Data was Found after GroupBy.",
                        exception=ex,
                        code=404
                    ) from ex
                except (QueryException, Exception) as ex:
                    raise self.Error(
                        message=f"Error on GroupBy: {ex!s}",
                        exception=ex
                    ) from ex
            if step_name == 'Processors':
                continue
        ### Step 4: Check if result is empty or is a dictionary of dataframes:
        if result is None:
            raise self.Error(
                message="Empty Result",
                code=404
            )
        if self._return_all is False and (isinstance(result, dict) and len(result) == 1):
            # reduce to one single Dataframe:
            result = list(result.values())[0]
        ### Step 5: Optionally saving result into Database (using Pandas)
        if _output:
            for step in _output:
                obj = None
                for step_name, component in step.items():
                    if step_name in ('tableOutput', 'TableOutput'):
                        obj = TableOutput(data=result, **component)
                        result = await obj.run()
        if result is None or len(result) == 0:
            raise DataNotFound(
                "QS Empty Result"
            )
        return result, self._options

    async def execute(self):
        """
        Execute the Query
        """
        return await self.query()
