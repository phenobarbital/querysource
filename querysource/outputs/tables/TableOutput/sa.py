from typing import List, Dict, Union, Optional, Any
from collections.abc import Callable
from sqlalchemy import MetaData, Table
from sqlalchemy.exc import ProgrammingError, OperationalError, StatementError
from sqlalchemy.inspection import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import ForeignKeyConstraint
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    AsyncConnection
)
from ....exceptions import OutputError
from .abstract import AbstractOutput


class SaOutput(AbstractOutput):
    def __init__(
        self,
        parent: Callable = None,
        dsn: str = None,
        do_update: bool = True,
        use_async: bool = False,
        returning_all: bool = False,
        **kwargs
    ) -> None:
        super(SaOutput, self).__init__(parent, dsn, do_update=do_update, **kwargs)

    def db_upsert(self, table, conn, keys, data_iter):
        """
        Execute SQL statement for upserting data

        Parameters
        ----------
        table : sqlalchemy.Table
        conn : sqlalchemy.engine.Engine or sqlalchemy.engine.Connection
        keys : list of str of Column names
        data_iter : Iterable that iterates the values to be inserted
        """
        args = []
        try:
            tablename = str(table.name)
        except Exception:
            tablename = self._parent.tablename
        if self._parent.foreign_keys():
            fk = self._parent.foreign_keys()
            fn = ForeignKeyConstraint(fk["columns"], fk["fk"], name=fk["name"])
            args.append(fn)
        metadata = MetaData()
        metadata.bind = conn
        constraint = self._parent.constraints()
        options = {"schema": self._parent.get_schema(), "autoload_with": conn}
        tbl = Table(table.name, metadata, *args, **options)
        # removing the columns from the table definition
        columns = self._columns
        # for column in columns:
        col_instances = [col for col in tbl._columns if col.name not in columns]
        for col in col_instances:
            tbl._columns.remove(col)

        primary_keys = []
        try:
            primary_keys = self._parent.primary_keys()
        except AttributeError as err:
            primary_keys = [key.name for key in inspect(tbl).primary_key]
            if not primary_keys:
                raise OutputError(
                    f"No Primary Key on table {tablename}."
                )
        for row in data_iter:
            row_dict = dict(zip(keys, row))
            if conn.dialect.name == 'postgresql':
                insert_stmt = postgresql.insert(tbl).values(**row_dict)
            else:
                insert_stmt = tbl.insert().values(**row_dict)  # Generic SQL insert
                # insert_stmt = conn.dialect().insert(tbl).values(**row_dict)
            # define dict of non-primary keys for updating
            # get the list of columns that are not part of the primary key
            # create a dictionary of the values to be updated
            update_dict = {
                c.name: c
                for c in insert_stmt.excluded
                if not c.primary_key and c.name in columns
            }
            if constraint is not None:
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    constraint=constraint, set_=update_dict
                )
                try:
                    conn.execute(upsert_stmt)
                except (ProgrammingError, OperationalError) as err:
                    raise OutputError(f"SQL Operational Error: {err}") from err
                except StatementError as err:
                    raise OutputError(f"Statement Error: {err}") from err
                except Exception as err:
                    raise OutputError(f"Error on SA UPSERT: {err}") from err

    async def close(self):
        """
        Close Database connection.

        """
        self._engine.dispose()

    def connect(self):
        self._engine = create_async_engine(self._dsn, echo=False)

    def write(
        self,
        table: str,
        schema: str,
        data: Union[List[Dict], Any],
        on_conflict: Optional[str] = 'replace',
        pk: List[str] = None
    ):
        raise NotImplementedError("Method not implemented")
