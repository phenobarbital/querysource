from collections.abc import Callable
from querysource.models import QueryObject
from querysource.types.validators import Entity
from querysource.types.typedefs import NullDefault, SafeDict
from querysource.providers import BaseProvider
from querysource.exceptions import (
    EmptySentence
)
from .abstract import QueryParser

class InfluxParser(QueryParser):
    def __init__(
        self,
        query: str = None,
        options: BaseProvider = None,
        connection: Callable = None,
        conditions: QueryObject = None,
        **kwargs
    ):
        self.bucket: str = None
        self.database: str = None
        super(InfluxParser, self).__init__(
            query=query,
            options=options,
            conditions=conditions,
            connection=connection,
            **kwargs
        )
        if options.source is not None:
            self.measurement = options.source
        else:
            self.measurement = self.database

    async def process_fields(self, query: str) -> str:
        # |> keep(columns: ["_time", "host", "program", "task", "message"])
        if isinstance(self.fields, list) and len(self.fields) > 0:
            ## adding Keep:
            if '_measurement' not in self.fields:
                self.fields.append('_measurement')
            fields = [Entity.dblQuoting(k) for k in self.fields]
            columns = ','.join(fields)
            keep = f"|> keep(columns: [{columns}])"
            query = f"{query} {keep}"
        return query

    async def filter_conditions(self, query: str) -> str:
        # |> filter(fn: (r) => r["_measurement"] == "fuel_tanks")
        if self.filter:
            self.logger.debug(f" == WHERE: {self.filter}")
            where_cond = []
            for key, value in self.filter.items():
                if isinstance(value, (str, int)):
                    val = value.replace("'", '')
                    where_cond.append(
                        f'|> filter(fn: (r) => r["{key}"] == "{val}")'
                    )
            _where = "".join(where_cond)
            query = f"{query} {_where}"
            print(where_cond)
        return query

    async def build_query(self, querylimit: int = None, offset: int = None):
        """
        build_query.
         Last Step: Build a SQL Query
        """
        query = self.query_raw
        self.logger.debug(f":: RAW QUERY: {query}")
        self.logger.debug(f"FIELDS ARE {self.fields}")
        self.logger.debug(f'Conditions ARE: {self.conditions}')
        query = await self.process_fields(query)
        # basic filtering:
        query = await self.filter_conditions(query)
        # removing other places:
        if self.conditions and len(self.conditions) > 0:
            query = query.format_map(SafeDict(**self.conditions))
        ## replacing bucket and measurement:
        args = {
            # "bucket": self.bucket,
            "measurement": self.measurement
        }
        query = query.format_map(SafeDict(**args))
        # at the end, default null setters
        query = query.format_map(NullDefault())
        ## adding "Sort" at the end:
        query = f"{query} |> sort()"
        self.query_parsed = query
        self.logger.debug(f": QUERY :: {query}")
        if self.query_parsed == '' or self.query_parsed is None:
            raise EmptySentence(
                'QS Influx: no query to parse.'
            )
        return self.query_parsed
