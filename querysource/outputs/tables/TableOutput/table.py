from typing import Union
import pandas as pd
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    OutputError
)
from .postgres import PgOutput
from .mysql import MysqlOutput
from .sa import SaOutput
from .rethink import RethinkOutput
from .bigquery import BigQueryOutput
from .mongodb import MongoDBOutput
from .documentdb import DocumentDBOutput


class TableOutput:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self._backend = 'pandas'
        self.data = data
        self._pk: list = []
        self._fk: str = None
        self._engine = None
        self._columns: list = []
        self._constraint: list = None
        self.flavor: str = kwargs.pop('flavor', 'postgresql')
        self._truncate: bool = kwargs.get('truncate', False)
        self.logger = logging.getLogger(
            f'QS.Output.{self.__class__.__name__}'
        )
        for k, v in kwargs.items():
            setattr(self, k, v)

    def foreign_keys(self):
        return self._fk

    def primary_keys(self):
        return self._pk

    def constraints(self):
        return self._constraint

    def get_schema(self):
        return self._schema

    async def table_output(self, elem, datasource: pd.DataFrame):
        # get info
        options = {
            'chunksize': 1000
        }
        table = elem.tablename
        try:
            schema = elem.schema
        except AttributeError:
            schema = 'public'
        if self._engine.is_external is False:
            # Using Pandas to_sql method:
            options['schema'] = schema
            # starting metric:
            if hasattr(elem, 'sql_options'):
                options = {**options, **elem.sql_options}
            if hasattr(elem, 'pk') or hasattr(elem, 'constraint'):
                options["index"] = False
            if hasattr(elem, 'if_exists'):
                options['if_exists'] = elem.if_exists
            else:
                options['if_exists'] = 'append'
            # define index:
            try:
                self._pk = elem.pk
                options['index_label'] = self._pk
            except AttributeError:
                self._pk = []
            # set the upsert method:
            options['method'] = self._engine.db_upsert
            self._fk = elem.foreign_key if hasattr(elem, 'foreign_key') else None
            self._constraint = elem.constraint if hasattr(elem, 'constraint') else None
            self._columns = list(datasource.columns)
            self._engine.columns = self._columns
            self._schema = schema
            # add metrics for Table Output
            u = datasource.select_dtypes(
                include=['object', 'string']
            )
            datasource[u.columns] = u.replace(['<NA>', 'None'], None)
            try:
                datasource.to_sql(
                    name=table,
                    con=self._engine.engine(),
                    **options
                )
                logging.debug(
                    f':: Saving Table Data {schema}.{table} ...'
                )
                return True
            except Exception:
                raise
        else:
            # Using Engine External method write:
            on_conflict = 'replace'
            if hasattr(elem, 'if_exists'):
                on_conflict = elem.if_exists
            await self._engine.db_upsert(  # pylint: disable=E1120,E1123 # noqa
                table=table,
                schema=schema,
                on_conflict=on_conflict
            )  # pylint: disable=E1120,E1123 # noqa

    async def run(self):
        # TODO: add a Truncate Method to every Engine
        if self.flavor == 'postgresql':
            self._engine = PgOutput(parent=self)
        elif self.flavor == 'mysql':
            self._engine = MysqlOutput(parent=self)
        elif self.flavor == 'sqlalchemy':
            self._engine = SaOutput(parent=self)
        elif self.flavor == 'rethink':
            self._engine == RethinkOutput(parent=self, external=True)
        elif self.flavor == 'bigquery':
            self._engine == BigQueryOutput(parent=self, external=True)
        elif self.flavor == 'mongodb':
            self._engine == MongoDBOutput(parent=self, external=True)
        elif self.flavor == 'documentdb':
            self._engine == DocumentDBOutput(parent=self, external=True)
        else:
            raise OutputError(
                f'TableOutput: unsupported DB flavor: {self.flavor}'
            )
        try:
            if isinstance(self.data, dict):
                for _, data in self.data.items():
                    ## TODO: add support for polars and datatables
                    if isinstance(data, pd.DataFrame):
                        self._backend = 'pandas'
                        if data.empty:
                            raise DataNotFound(
                                "Empty Dataframe"
                            )
                        # Send to Table Output
                        await self.table_output(self, data)
                    else:
                        raise DriverError(
                            f'Wrong type of data: required a Pandas dataframe: {type(data)}'
                        )
                return self.data
            elif isinstance(self.data, pd.DataFrame):
                # Send to Table Output
                await self.table_output(self, self.data)
                return self.data
            else:
                raise DriverError(
                    f'Wrong type of data, required a Pandas dataframe: {type(data)}'
                )
        finally:
            try:
                self._engine.close()
            except Exception as err:
                logging.error(err)
