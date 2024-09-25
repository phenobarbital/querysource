from collections.abc import Callable
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import ForeignKeyConstraint
from sqlalchemy import MetaData, Table
from sqlalchemy.inspection import inspect
from sqlalchemy.exc import ProgrammingError, OperationalError, StatementError
from .....conf import sqlalchemy_url
from .....exceptions import OutputError
from .abstract import AbstractOutput


class PgOutput(AbstractOutput):
    """PgOutput.

    Class for writing output to postgresql database.

    Used by Pandas to_sql statement.
    """
    def __init__(
        self,
        parent: Callable,
        dsn: str = None,
        do_update: bool = True,
        **kwargs
    ) -> None:
        if not dsn:
            dsn = sqlalchemy_url
        super().__init__(parent, dsn, do_update=do_update, **kwargs)

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
            col for col in tbl._columns if col.name not in columns
        ]
        # Removing the columns not involved in query
        for col in col_instances:
            tbl._columns.remove(col)

        primary_keys = []
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
            if self._do_update is True:
                if len(columns) > 1:
                    # TODO: add behavior of on_conflict_do_nothing
                    update_dict = {
                        c.name: c
                        for c in insert_stmt.excluded
                        if not c.primary_key and c.name in columns
                    }
                    if constraint is not None:
                        upsert_stmt = insert_stmt.on_conflict_do_update(
                            constraint=constraint, set_=update_dict
                        )
                    else:
                        upsert_stmt = insert_stmt.on_conflict_do_update(
                            index_elements=primary_keys, set_=update_dict
                        )
                else:
                    upsert_stmt = insert_stmt.on_conflict_do_nothing(
                        index_elements=primary_keys
                    )
            else:
                # Do nothing on conflict
                upsert_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=primary_keys
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
                raise OutputError(
                    f"Error on PG UPSERT: {err}"
                ) from err
