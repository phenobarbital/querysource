from typing import Union
import pandas as pd
from navconfig.logging import logging
from ....exceptions import (
    DataNotFound,
    DriverError,
    OutputError
)
from .postgres import PgOutput


class TableOutput:
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self._backend = 'pandas'
        self.data = data
        self._pk: list = []
        self._fk: str = None
        self._engine = None
        self._columns: list = []
        self._constraint: list = None
        try:
            self.flavor = kwargs['flavor']
            del kwargs['flavor']
        except KeyError:
            self.flavor = 'postgresql'
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
        if hasattr(elem, 'foreign_key'):
            self._fk = elem.foreign_key
        else:
            self._fk = None
        if hasattr(elem, 'constraint'):
            self._constraint = elem.constraint
        else:
            self._constraint = None
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

    async def run(self):
        if self.flavor == 'postgresql':
            self._engine = PgOutput(parent=self)
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
