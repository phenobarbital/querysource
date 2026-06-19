import asyncio
from typing import Set, Union
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
        self._jsonb_columns: Set[str] = set(kwargs.pop('jsonb_columns', []) or [])
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

    # Alias used by engine parent-protocol for FEAT-097 compatibility.
    # Both TableOutput (legacy) and TableDestination/DWHDestination (new)
    # expose sql_schema() so the engine files can call a single method.
    def sql_schema(self):
        return self._schema

    @property
    def jsonb_columns(self) -> Set[str]:
        return self._jsonb_columns

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
                engine = self._engine.engine()
                has_pk = bool(self._pk) and hasattr(self._engine, 'ensure_primary_key')
                if self._truncate and hasattr(self._engine, 'truncate_table'):
                    # Keep the table (grants/indexes/comments), wipe rows,
                    # then upsert. Create the structure + PK first if missing.
                    datasource.head(0).to_sql(
                        name=table,
                        con=engine,
                        schema=schema,
                        if_exists='append',
                        index=False,
                    )
                    if has_pk:
                        self._engine.ensure_primary_key(schema, table, self._pk)
                    self._engine.truncate_table(schema, table)
                    datasource.to_sql(
                        name=table,
                        con=engine,
                        **{**options, 'if_exists': 'append'}
                    )
                elif has_pk:
                    # pandas.to_sql never creates a PK, but db_upsert needs one
                    # for its ON CONFLICT target. Split the write into:
                    #   1) create the (empty) structure honouring if_exists,
                    #   2) add the PRIMARY KEY declared in the JSON config,
                    #   3) insert the data via append + the db_upsert method.
                    datasource.head(0).to_sql(
                        name=table,
                        con=engine,
                        schema=schema,
                        if_exists=options.get('if_exists', 'append'),
                        index=False,
                    )
                    self._engine.ensure_primary_key(schema, table, self._pk)
                    datasource.to_sql(
                        name=table,
                        con=engine,
                        **{**options, 'if_exists': 'append'}
                    )
                else:
                    datasource.to_sql(
                        name=table,
                        con=engine,
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
                data=datasource,
                table=table,
                schema=schema,
                on_conflict=on_conflict
            )  # pylint: disable=E1120,E1123 # noqa

    async def run(self):
        # TODO: add a Truncate Method to every Engine
        try:
            if self.flavor in ('postgresql', 'postgres'):
                self._engine = PgOutput(parent=self, jsonb_columns=self._jsonb_columns)
            elif self.flavor == 'mysql':
                self._engine = MysqlOutput(parent=self)
            elif self.flavor == 'sqlalchemy':
                self._engine = SaOutput(parent=self)
            elif self.flavor == 'rethink':
                self._engine = RethinkOutput(parent=self, external=True)
            elif self.flavor == 'bigquery':
                self._engine = BigQueryOutput(parent=self, external=True)
            elif self.flavor == 'mongodb':
                self._engine = MongoDBOutput(parent=self, external=True)
            elif self.flavor == 'documentdb':
                self._engine = DocumentDBOutput(parent=self, external=True)
            else:
                raise OutputError(
                    f'TableOutput: unsupported DB flavor: {self.flavor}'
                )
        except Exception as err:
            raise OutputError(
                f'TableOutput: Engine Error: {err}'
            ) from err
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
                try:
                    await self.table_output(self, self.data)
                except Exception as err:
                    raise OutputError(
                        f'TableOutput Error: {err}'
                    ) from err
                return self.data
            else:
                raise DriverError(
                    f'Wrong type of data, required a Pandas dataframe: {type(data)}'
                )
        finally:
            try:
                if asyncio.iscoroutinefunction(self._engine.close):
                    await self._engine.close()
                else:
                    self._engine.close()
            except Exception as err:
                logging.error(err)
