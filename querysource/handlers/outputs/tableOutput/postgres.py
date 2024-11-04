from collections.abc import Callable
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import ForeignKeyConstraint
from sqlalchemy.pool import NullPool
from sqlalchemy import create_engine, MetaData, Table
from sqlalchemy.inspection import inspect
from sqlalchemy.exc import ProgrammingError, OperationalError, StatementError
from navconfig.logging import logging
from ....conf import sqlalchemy_url
from ....exceptions import OutputError


class PgOutput(object):

    def __init__(self, parent: Callable) -> None:
        self._engine: Callable = None
        self._parent = parent
        self._results: list = []
        self._columns: list = []
        try:
            self._engine = create_engine(
                sqlalchemy_url, echo=False, poolclass=NullPool
            )
        except Exception as err:
            logging.exception(err, stack_info=True)
            raise OutputError(
                message=f"Connection Error: {err}"
            ) from err

    def engine(self):
        return self._engine

    def close(self):
        """Closing Operations."""
        try:
            self._engine.dispose()
        except Exception as err:
            logging.error(err)

    @property
    def columns(self):
        return self._columns

    @columns.setter
    def columns(self, columns: list):
        self._columns = columns

    def db_upsert(self, table, conn, keys, data_iter):
        """
            Execute SQL statement for upserting data

            Parameters
            ----------
            table : pandas.io.sql.SQLTable
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
            fn = ForeignKeyConstraint(
                fk['columns'],
                fk['fk'],
                name=fk['name']
            )
            args.append(fn)
        metadata = MetaData()
        metadata.bind = self._engine
        constraint = self._parent.constraints()
        options = {
            'schema': self._parent.get_schema(),
            "autoload_with": self._engine
        }
        tbl = Table(tablename, metadata, *args, **options)
        # get list of fields making up primary key
        # removing the columns from the table definition
        # columns = self._parent.columns
        columns = self._columns
        # for column in columns:
        col_instances = [
            col for col in tbl._columns if col.name not in columns]
        for col in col_instances:
            tbl._columns.remove(col)
        try:
            primary_keys = self._parent.primary_keys()
        except AttributeError as err:
            primary_keys = [key.name for key in inspect(tbl).primary_key]
            if not primary_keys:
                raise OutputError(
                    f'No Primary Key on table {tablename}.'
                ) from err
        for row in data_iter:
            row_dict = dict(zip(keys, row))
            insert_stmt = postgresql.insert(tbl).values(**row_dict)
            # define dict of non-primary keys for updating
            update_dict = {
                c.name: c
                for c in insert_stmt.excluded
                if not c.primary_key and c.name in columns
            }
            if constraint is not None:
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    constraint=constraint,
                    set_=update_dict
                )
            else:
                upsert_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=primary_keys,
                    set_=update_dict
                )
            try:
                conn.execute(upsert_stmt)
            except (ProgrammingError, OperationalError) as err:
                raise OutputError(
                    f"SQL Operational Error: {err}"
                ) from err
            except (StatementError) as err:
                raise OutputError(
                    f"Statement Error: {err}"
                ) from err
            except Exception as err:
                if 'Unconsumed' in str(err):
                    error = f"""
                    There are missing columns on Table {tablename}.

                    Error was: {err}
                    """
                    raise OutputError(
                        error
                    ) from err
                raise OutputError(
                    f"Error on PG UPSERT: {err}"
                ) from err
