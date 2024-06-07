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
from ..abstract import BaseQuery
from .operators import Join, Concat, Melt
from .transformations import (
    crosstab,
    correlation,
    GoogleMaps,
    Forecast,
    Map
)
from .outputs import TableOutput
from .sources import ThreadQuery, ThreadFile


class MultiQS(BaseQuery):
    """
    MultiQS.

       Query multiple data-origins or files in QuerySource.
    """
    def __init__(
            self,
            queries: Optional[list] = None,
            files: Optional[list] = None,
            query: Optional[dict] = None,
            conditions: dict = None,
            request: web.Request = None,
            loop: asyncio.AbstractEventLoop = None,
            **kwargs
    ):
        super(MultiQS, self).__init__(
            slug=None,
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
        if not (self._queries or self._files):  # Check if both are effectively empty
            raise DriverError(
                'Invalid Option passed to MultiQuery.'
            )

    async def query(self):
        """
        Executing Multiple Queries/Files
        """
        tasks = {}
        if self._queries:
            for name, query in self._queries.items():
                t = ThreadQuery(
                    name, query, self._request, self._queue
                )
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
                ## raise exception for this Task
                if isinstance(t.exc, SlugNotFound):
                    raise SlugNotFound(
                        f"Slug Not Found: {t.slug()}"
                    )
                elif isinstance(t.exc, ParserError):
                    raise self.Error(
                        f"Error parsing Query Slug {t.slug()}",
                        exception=t.exc
                    )
                elif isinstance(t.exc, (QueryException, DriverError)):
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
        ### Step 2: passing Results to JOIN virtuals
        if 'Join' in self._options:
            try:
                ## making Join of Data
                _join = self._options.get('Join', {})
                if isinstance(_join, dict):
                    join = Join(data=result, **_join)
                    result = await join.run()
                elif isinstance(_join, list):
                    for j in _join:
                        join = Join(data=result, **j)
                        result = await join.run()
            except DataNotFound:
                raise
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message="Error on JOIN",
                    exception=ex
                ) from ex
        if 'Concat' in self._options:
            try:
                ## making Join of Data
                concat = Concat(data=result, **self._options['Concat'])
                result = await concat.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message="Error on Concat",
                    exception=ex
                ) from ex
        if 'Melt' in self._options:
            try:
                ## making Join of Data
                melt = Melt(data=result, **self._options['Melt'])
                result = await melt.run()
            except (QueryException, Exception) as ex:
                raise self.Error(
                    message=f"Error on Melting Data: {ex}",
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
            ## TODO: logic for calling components:
            for step in self._options['Transform']:
                obj = None
                for step_name, component in step.items():
                    if step_name == 'crosstab':
                        obj = crosstab(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'correlation':
                        obj = correlation(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'GoogleMaps':
                        obj = GoogleMaps(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'Forecast':
                        obj = Forecast(data=result, **component)
                        result = await obj.run()
                    elif step_name == 'Map':
                        obj = Map(data=result, **component)
                        result = await obj.run()
                continue
        if 'Processors' in self._options:
            pass
        ### Step 4: Check if result is empty or is a dictionary of dataframes:
        if result is None:
            raise self.Error(
                message="Empty Result",
                code=404
            )
        # reduce to one single Dataframe:
        if isinstance(result, dict) and len(result) == 1:
            result = list(result.values())[0]
        # TODO: making a melt of all dataframes
        ### Step 5: Passing result to any Processor declared
        if 'Output' in self._options:
            ## Optionally saving result into Database (using Pandas)
            for step in self._options['Output']:
                obj = None
                for step_name, component in step.items():
                    if step_name == 'tableOutput':
                        obj = TableOutput(data=result, **component)
                        result = await obj.run()
        if result is None or len(result) == 0:
            raise DataNotFound(
                "QS Empty Result"
            )
        return result
