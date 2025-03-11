from typing import Union, Any, Dict, List, Optional
from collections.abc import Callable
import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import ForeignKeyConstraint
from sqlalchemy import (
    Table,
    MetaData,
    create_engine,
    Column,
    text
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncEngine,
    AsyncConnection
)
from sqlalchemy.pool import NullPool
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.exc import (
    ProgrammingError,
    OperationalError,
    StatementError
)
import dataclasses
from datamodel import BaseModel
from ....conf import (
    sqlalchemy_url,
    async_default_dsn
)
from ....exceptions import OutputError
from .abstract import AbstractOutput


class PgOutput(AbstractOutput):
    """PgOutput.

    Class for writing output to postgresql database.

    Used by Pandas to_sql statement.
    """
    def __init__(
        self,
        parent: Callable = None,
        dsn: str = None,
        do_update: bool = True,
        use_async: bool = False,
        returning_all: bool = False,
        **kwargs
    ) -> None:
        """Initialize with database connection string.

        Parameters
        ----------
        dsn : str
            Database connection string for asyncpg
        do_update : bool, default True
            Whether to update existing rows (True) or do nothing (False)
        returning_all : bool, default False
            Whether to return all columns after insert/update operations (RETURNING *)
        """
        if not dsn:
            if use_async:
                dsn = async_default_dsn
            else:
                dsn = sqlalchemy_url
        self._dsn = dsn
        super().__init__(parent, dsn, do_update=do_update, **kwargs)
        # Create an async Engine instance:
        self.use_async = use_async
        self._returning_all = returning_all
        if use_async is False:
            try:
                self._engine = create_engine(dsn, echo=False, poolclass=NullPool)
            except Exception as err:
                self.logger.exception(err, stack_info=True)
                raise OutputError(
                    message=f"Connection Error: {err}"
                ) from err

    def connect(self):
        self._engine = create_async_engine(self._dsn, echo=False)

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
            primary_keys = [key.name for key in sa_inspect(tbl).primary_key]
            if not primary_keys:
                raise OutputError(
                    f'No Primary Key on table {tablename}.'
                ) from err
        for row in data_iter:
            row_dict = dict(zip(keys, row))
            insert_stmt = postgresql.insert(tbl).values(**row_dict)
            # define dict of non-primary keys for updating
            if self._do_update:
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

    async def do_upsert(
        self,
        obj: Union[Dict[str, Any], Any],
        table_name: Optional[str] = None,
        schema: Optional[str] = None,
        primary_keys: Optional[List[str]] = None,
        constraint: Optional[str] = None,
        foreign_keys: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Upsert a dictionary or dataclass object into PostgreSQL.

        Parameters
        ----------
        obj : Union[Dict[str, Any], Any]
            Dictionary or dataclass object to insert/update
        table_name : str
            Name of the target table
        schema : str
            Database schema name
        primary_keys : Optional[List[str]], default None
            List of primary key column names. If None, will try to determine from table
        constraint : Optional[str], default None
            Named constraint to use for conflict resolution
        foreign_keys : Optional[Dict[str, Any]], default None
            Dictionary containing foreign key information with keys:
            - 'columns': columns in this table
            - 'fk': referenced columns
            - 'name': constraint name
        """
        # Convert dataclass to dict if needed
        if isinstance(obj, BaseModel):
            data = obj.to_dict(as_values=True, convert_enums=True)
            if table_name is None:
                table_name = obj.Meta.table
            if schema is None:
                schema = obj.Meta.schema
        elif dataclasses.is_dataclass(obj) and not isinstance(obj, dict):
            data = dataclasses.asdict(obj)
        elif isinstance(obj, dict):
            data = obj
        else:
            # Try to convert object to dict by getting attributes
            data = {
                k: v for k, v in inspect.getmembers(obj)
                if not k.startswith('_') and not callable(v)
            }

        if table_name is None:
            raise ValueError(
                "Table name must be provided or available from the object's Meta class"
            )

        if schema is None:
            schema = 'public'
            self.logger.warning(
                f"Schema not provided. Defaulting to '{schema}' schema."
            )

        # Create metadata and table reference
        metadata = MetaData()
        args = []

        # Add foreign key constraints if provided
        if foreign_keys:
            fk = foreign_keys
            fn = ForeignKeyConstraint(
                fk['columns'],
                fk['fk'],
                name=fk['name']
            )
            args.append(fn)

        # Get column names from the data
        columns = list(data.keys())

        # Connect to database and execute upsert
        async with self._engine.begin() as conn:
            # Get table definition with reflection
            pk_columns = []
            if primary_keys is None:
                # Get primary key columns through a SQL query
                table_with_schema = f'"{schema}"."{table_name}"'
                pk_query = text(f"""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = '"{schema}"."{table_name}"'::regclass
                    AND i.indisprimary;
                """)

                # Execute query to get primary keys
                pk_result = await conn.execute(pk_query)
                pk_rows = pk_result.fetchall()

                if not pk_rows:
                    raise ValueError(
                        f"No primary key found for table: {table_with_schema}"
                    )

                pk_columns = [row[0] for row in pk_rows]
            else:
                pk_columns = primary_keys

            # Get all column names from the table
            cols_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema
                AND table_name = :table_name;
            """)

            # Execute query to get all columns
            cols_result = await conn.execute(
                cols_query, {"schema": schema, "table_name": table_name}
            )
            cols_rows = cols_result.fetchall()

            if not cols_rows:
                raise ValueError(
                    f"Table {schema}.{table_name} not found or has no columns"
                )

            valid_columns = [row[0] for row in cols_rows]

            # Filter data to include only valid columns
            filtered_data = {k: v for k, v in data.items() if k in valid_columns}

            if not filtered_data:
                raise ValueError(
                    f"No valid columns found in data for table {schema}.{table_name}"
                )

            # Now construct the upsert statement using the PostgreSQL dialect
            # but without reflecting the table structure
            column_set = set(filtered_data.keys())

            # Create a minimal table definition with just the columns we need
            table = Table(
                table_name,
                metadata,
                schema=schema,
                *(Column(name) for name in column_set.union(set(pk_columns)))
            )

            # Create insert statement
            insert_stmt = postgresql.insert(table).values(**filtered_data)

            if self._do_update:
                # Define dict of non-primary keys for updating
                update_dict = {}
                for col_name in filtered_data:
                    # Skip primary key columns
                    if col_name in pk_columns:
                        continue
                    # Add to update dict with excluded reference
                    update_dict[col_name] = insert_stmt.excluded[col_name]

                if update_dict:  # Only update if there are non-primary key columns
                    if constraint is not None:
                        upsert_stmt = insert_stmt.on_conflict_do_update(
                            constraint=constraint,
                            set_=update_dict
                        )
                    else:
                        upsert_stmt = insert_stmt.on_conflict_do_update(
                            index_elements=pk_columns,
                            set_=update_dict
                        )
                else:
                    upsert_stmt = insert_stmt.on_conflict_do_nothing(
                        index_elements=pk_columns
                    )
            else:
                # Do nothing on conflict
                upsert_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=pk_columns
                )

            # Add RETURNING * if returning_all is True
            if self._returning_all:
                upsert_stmt = upsert_stmt.returning(*[table.c[col] for col in valid_columns])

            try:
                result = await conn.execute(upsert_stmt)
                # Get the result information
                if result.returns_rows:
                    # If the statement returns rows (like RETURNING clause), fetch them
                    rows = result.fetchall()
                    return rows
                else:
                    # For INSERT/UPDATE without RETURNING, get rowcount
                    return {"rowcount": result.rowcount, "status": "success"}
            except (ProgrammingError, OperationalError) as err:
                raise ValueError(f"SQL Operational Error: {err}") from err
            except StatementError as err:
                raise ValueError(f"Statement Error: {err}") from err
            except Exception as err:
                if 'Unconsumed' in str(err):
                    error = f"""
                    There are missing columns on Table {table_name}.

                    Error was: {err}
                    """
                    raise ValueError(error) from err
                raise ValueError(f"Error on PG UPSERT: {err}") from err

    async def upsert_many(
        self,
        objects: List[Union[Dict[str, Any], Any]],
        table_name: str,
        schema: str,
        primary_keys: Optional[List[str]] = None,
        constraint: Optional[str] = None,
        foreign_keys: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Upsert multiple dictionary or dataclass objects into PostgreSQL.

        Parameters
        ----------
        objects : List[Union[Dict[str, Any], Any]]
            List of dictionary or dataclass objects to insert/update
        table_name : str
            Name of the target table
        schema : str
            Database schema name
        primary_keys : Optional[List[str]], default None
            List of primary key column names. If None, will try to determine from table
        constraint : Optional[str], default None
            Named constraint to use for conflict resolution
        foreign_keys : Optional[Dict[str, Any]], default None
            Dictionary containing foreign key information with keys:
            - 'columns': columns in this table
            - 'fk': referenced columns
            - 'name': constraint name

        Returns
        -------
        List[Any]
            Results of the execute operations
        """
        if not objects:
            return

        results = []
        for obj in objects:
            result = await self.upsert_object(
                obj=obj,
                table_name=table_name,
                schema=schema,
                primary_keys=primary_keys,
                constraint=constraint,
                foreign_keys=foreign_keys
            )
            results.append(result)
        return results

    async def close(self):
        """Close the database engine."""
        try:
            if self.use_async:
                await self._engine.dispose()
            else:
                self._engine.dispose()
        except Exception as err:
            self.logger.error(err)
            raise OutputError(
                f"Error closing database connection: {err}"
            ) from err

    def write(
        self,
        table: str,
        schema: str,
        data: Union[List[Dict], Any],
        on_conflict: Optional[str] = 'replace',
        pk: List[str] = None
    ):
        raise NotImplementedError("Method not implemented")
