from typing import Union, Any, Dict, List, Optional, Set
from collections.abc import Callable
import asyncio
import inspect
from copy import deepcopy
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import ForeignKeyConstraint
from sqlalchemy import (
    Table,
    MetaData,
    create_engine,
    Column,
    text,
    and_
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
from datamodel.parsers.json import json_encoder
from ....conf import (
    sqlalchemy_url,
    async_default_dsn
)
from ....exceptions import OutputError
from .abstract import AbstractOutput
from ....conf import TIMEZONE


class ReflectionHelper:
    """
    Helper for making reflection and instrospection of Database Objects.
    """
    _table: Dict[str, Dict] = {}
    _columns: Dict[str, Set[str]] = {}
    _pk_columns: Dict[str, List[str]] = {}

    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._is_async = hasattr(engine, 'run_sync')

    async def get_table(
        self,
        table_name: str,
        schema: str = 'public',
        primary_keys: list = None
    ) -> dict:
        table = f'{schema}.{table_name}'
        if table in self._table:
            return self._table[table]
        else:
            # Build the Table Definition:
            metadata = MetaData()
            metadata.bind = self._engine
            async with self._engine.begin() as conn:
                # Get table definition with reflection
                pk_columns = []
                if not primary_keys:
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
                            f"No primary key found for table: {table}"
                        )
                    pk_columns = [row[0] for row in pk_rows]
                else:
                    pk_columns = primary_keys
                # Reflect valid columns
                cols_query = text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema
                    AND table_name = :table_name;
                """)
                cols_result = await conn.execute(
                    cols_query, {"schema": schema, "table_name": table_name}
                )
                cols_rows = cols_result.fetchall()
                if not cols_rows:
                    raise ValueError(
                        f"Table {schema}.{table_name} not found or has no columns"
                    )
                valid_columns = {row[0] for row in cols_rows}
                table_columns = set(valid_columns)
                # Create a minimal table definition with just the columns we need
                definition = Table(
                    table_name,
                    metadata,
                    schema=schema,
                    *(Column(name) for name in table_columns.union(set(pk_columns)))
                )
                result = {
                    "table": definition,
                    "columns": valid_columns,
                    "pk_columns": pk_columns
                }
                self._table[table] = result
                return result

    def get_table_sync(
        self,
        table_name: str,
        schema: str = 'public',
        primary_keys: Optional[List[str]] = None,
        use_cache: bool = True,
        **options
    ) -> Dict:
        """
        Synchronous version of get_table for non-async engines.
        """
        table_key = f'{schema}.{table_name}'
        if use_cache and table_key in self._table:
            return self._table[table_key]

        # Build the Table Definition:
        metadata = MetaData()
        metadata.bind = self._engine

        with self._engine.connect() as conn:
            # Get primary keys if not provided
            pk_columns = []
            if not primary_keys:
                if table_key in self._pk_columns:
                    pk_columns = self._pk_columns[table_key]
                else:
                    pk_query = text(f"""
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                        WHERE i.indrelid = '"{schema}"."{table_name}"'::regclass
                        AND i.indisprimary;
                    """)
                    pk_result = conn.execute(pk_query)
                    pk_rows = pk_result.fetchall()
                    if not pk_rows:
                        raise ValueError(f"No primary key found for table: {table_key}")
                    pk_columns = [row[0] for row in pk_rows]
                    self._pk_columns[table_key] = pk_columns
            else:
                pk_columns = primary_keys
                self._pk_columns[table_key] = pk_columns

            # Get valid columns
            if table_key in self._columns:
                valid_columns = self._columns[table_key]
            else:
                cols_query = text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema
                    AND table_name = :table_name;
                """)
                cols_result = conn.execute(
                    cols_query, {"schema": schema, "table_name": table_name}
                )
                cols_rows = cols_result.fetchall()
                if not cols_rows:
                    raise ValueError(f"Table {schema}.{table_name} not found or has no columns")
                valid_columns = {row[0] for row in cols_rows}
                self._columns[table_key] = valid_columns

            # Create a minimal table definition
            definition = Table(
                table_name,
                metadata,
                schema=schema,
                **options
            )

            result = {
                "table": definition,
                "columns": valid_columns,
                "pk_columns": pk_columns
            }
            self._table[table_key] = result
            return result

    def get_table_def(
        self,
        table_name: str,
        schema: str = 'public',
        primary_keys: Optional[List[str]] = None,
        use_cache: bool = True,
        **kwargs
    ) -> Dict:
        """
        Get table information, choosing async or sync method based on engine type.
        """
        if self._is_async:
            # We need to run the async function in a synchronous context
            # This is a bit of a hack but works for pandas to_sql integration
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already in an async context, create a new task
                return asyncio.create_task(self.get_table(table_name, schema, primary_keys))
            else:
                # We're in a sync context, run the async function to completion
                return loop.run_until_complete(self.get_table(table_name, schema, primary_keys))
        else:
            return self.get_table_sync(table_name, schema, primary_keys, use_cache, **kwargs)

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
        only_update: bool = False,
        use_async: bool = False,
        returning_all: bool = False,
        batch_size: int = 100,
        timezone: Optional[str] = None,
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
        dsn = async_default_dsn if use_async else sqlalchemy_url
        self._dsn = dsn
        self.use_cache: bool = kwargs.get('use_cache', False)
        super().__init__(parent, dsn, do_update=do_update, only_update=only_update, **kwargs)
        # Create an async Engine instance:
        self.use_async = use_async
        self._returning_all = returning_all
        self._tz: str = timezone or TIMEZONE
        self._helper: Any = None
        self._connection = None
        self._batch_size = batch_size
        if not use_async:
            try:
                self._engine = create_engine(dsn, echo=False, poolclass=NullPool)
                self._helper = ReflectionHelper(self._engine)
            except Exception as err:
                self.logger.exception(err, stack_info=True)
                raise OutputError(
                    message=f"Connection Error: {err}"
                ) from err
        else:
            self._engine = create_async_engine(
                self._dsn,
                echo=False,
                pool_size=30,
                max_overflow=10,
                pool_timeout=10,
                pool_pre_ping=True,
                connect_args={"server_settings": {"timezone": "UTC"}}
            )
            self._helper = ReflectionHelper(self._engine)

    def connect(self):
        return self

    async def open(self):
        """
        Open Database connection.
        """
        try:
            if self._connection is None or self._connection.closed:
                self._connection = await self._engine.connect()
        except Exception as err:
            self.logger.error(
                f"Error opening database connection: {err}"
            )
            raise OutputError(
                f"Error opening database connection: {err}"
            ) from err

    def _build_upsert_statement(
        self,
        table: Table,
        keys: List[str],
        values_list: List[tuple],
        primary_keys: List[str],
        constraint: Optional[str] = None
    ):
        """
        Build an efficient upsert statement for multiple rows.

        Parameters:
        -----------
        table : sqlalchemy.Table
            SQLAlchemy table object
        keys : list[str]
            Column names for the insert
        values_list : list[tuple]
            List of value tuples to insert
        primary_keys : list[str]
            List of primary key column names
        constraint : str, optional
            Constraint name to use instead of primary keys

        Returns:
        --------
        sqlalchemy.sql.dml.Insert
            The prepared upsert statement
        """
        # Prepare the multi-value insert statement
        insert_stmt = postgresql.insert(table).values([
            dict(zip(keys, values)) for values in values_list
        ])

        # If we're doing an update on conflict
        if self._do_update:
            update_dict = {
                c.name: c
                for c in insert_stmt.excluded
                if c.name in keys and c.name not in primary_keys
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
        else:
            # Do nothing on conflict
            upsert_stmt = insert_stmt.on_conflict_do_nothing(
                index_elements=primary_keys
            )

        return upsert_stmt

    def _build_update_statement(self, table, keys, values_list, primary_keys):
        """
        Build update statements for the batch of rows.
        Since UPDATE doesn't support multi-value syntax in PostgreSQL,
        we need to create individual statements.

        Parameters are the same as _build_upsert_statement.

        Returns:
        --------
        list[sqlalchemy.sql.dml.Update]
            List of update statements
        """
        update_statements = []
        for values in values_list:
            row_dict = dict(zip(keys, values))

            # Build conditions for WHERE clause
            conditions = []
            conditions.extend(getattr(table.c, pk) == row_dict[pk] for pk in primary_keys)

            # Combine them into a single AND condition
            where_clause = and_(*conditions)

            # Create update statement
            update_stmt = (
                table.update()
                .where(where_clause)
                .values(**row_dict)
            )
            update_statements.append(update_stmt)

        return update_statements

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

        schema = self._parent.get_schema()
        primary_keys = self._parent.primary_keys()
        constraint = self._parent.constraints()

        if self._parent.foreign_keys():
            fk = self._parent.foreign_keys()
            fn = ForeignKeyConstraint(
                fk['columns'],
                fk['fk'],
                name=fk['name']
            )
            args.append(fn)

        # Create table reference
        options = {
            "autoload_with": self._engine
        }
        tbl = self._helper.get_table_def(
            tablename,
            schema=schema,
            primary_keys=primary_keys,
            use_cache=self.use_cache,
            **options
        )
        # Get the table object (copied):
        tableobj = deepcopy(tbl)

        # Get the table and primary key columns
        tbl = deepcopy(tableobj.get('table'))
        pk_columns = tableobj['pk_columns']

        if not primary_keys:
            primary_keys = pk_columns

        # get list of fields making up primary key
        # removing the columns from the table definition
        # columns = self._parent.columns
        columns = self._columns
        # Removing the columns not involved in query
        for c in list(tbl.columns):
            if c.name not in columns:
                tbl._columns.remove(c)

        # Process data in batches
        batch_size = self._batch_size
        batch_values = []

        for row in data_iter:
            batch_values.append(row)
            if len(batch_values) >= batch_size:
                # When batch size is reached, execute the batch
                if self._only_update:
                    # Build a standard UPDATE ... WHERE field=...
                    update_statements = self._build_update_statement(
                        tbl, keys, batch_values, primary_keys
                    )
                    for stmt in update_statements:
                        self._execute(stmt, conn, tablename)
                else:
                    upsert_stmt = self._build_upsert_statement(
                        tbl, keys, batch_values, primary_keys, constraint
                    )
                    self._execute(upsert_stmt, conn, tablename)
                # Clear the batch
                batch_values = []
        # Process any remaining rows
        if batch_values:
            if self._only_update:
                update_statements = self._build_update_statement(
                    tbl, keys, batch_values, primary_keys
                )
                for stmt in update_statements:
                    self._execute(stmt, conn, tablename)
            else:
                upsert_stmt = self._build_upsert_statement(
                    tbl, keys, batch_values, primary_keys, constraint
                )
                self._execute(upsert_stmt, conn, tablename)

    def _execute(self, stmt, conn, tablename: str):
        try:
            conn.execute(stmt)
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

    async def _get_connection(self, use_conn=None):
        """
        Get an existing connection or create a new one.

        Parameters
        ----------
        use_conn : AsyncConnection, optional
            An existing connection to use

        Returns
        -------
        tuple
            (connection, own_transaction) where own_transaction indicates if we created the connection
        """
        own_transaction = False

        if use_conn:
            return use_conn, own_transaction

        if self._connection is not None and not self._connection.closed:
            return self._connection, own_transaction

        # We need a new connection
        try:
            conn = await self._engine.connect()
            own_transaction = True
            return conn, own_transaction
        except Exception as err:
            self.logger.error(
                f"Error getting database connection: {err}"
            )
            raise OutputError(
                f"Error getting database connection: {err}"
            ) from err

    async def do_upsert(
        self,
        obj: Union[Dict[str, Any], Any],
        table_name: Optional[str] = None,
        schema: Optional[str] = None,
        primary_keys: Optional[List[str]] = None,
        constraint: Optional[str] = None,
        foreign_keys: Optional[Dict[str, Any]] = None,
        as_values: bool = True,
        use_conn: Any = None,
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
            if as_values:
                data = obj.to_dict(as_values=True, convert_enums=True)
                # Remove any keys that start with underscore (threatened by dataclasses)
                # and are not part of the model
                data = {k: v for k, v in data.items() if not k.startswith('_')}
            else:
                data = obj.to_dict(convert_enums=True)
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

        # Create table reference
        tableobj = await self._helper.get_table(
            table_name,
            schema=schema,
            primary_keys=primary_keys
        )
        table = tableobj['table']
        pk_columns = tableobj['pk_columns']
        valid_columns = tableobj['columns']

        # Filter data to include only valid columns
        filtered_data = {k: v for k, v in data.items() if k in valid_columns}

        # Add debugging to see what we're trying to insert
        # self.logger.debug(
        #     f"Upserting into {schema}.{table_name} with columns: {list(filtered_data.keys())}"
        # )

        if not filtered_data:
            raise ValueError(
                f"No valid columns found in data for table {schema}.{table_name}"
            )

        # Get the columns from filtered_data
        columns = list(filtered_data.keys())

        # Create insert statement
        insert_stmt = postgresql.insert(table).values(**filtered_data)

        if self._do_update:
            if len(columns) == 1:
                upsert_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=primary_keys
                )
            else:
                # Define dict of non-primary keys for updating
                update_dict = {
                    c.name: c
                    for c in insert_stmt.excluded
                    if c.name in columns and not c.primary_key
                }
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
            upsert_stmt = upsert_stmt.returning(
                *[table.c[col] for col in valid_columns]
            )
        # Track connection and transaction ownership
        own_transaction = False
        try:
            if use_conn:
                conn = use_conn
            else:
                if self._connection is not None:
                    conn = self._connection
                else:
                    # conn = await self._engine.connect()
                    # own_transaction = True
                    conn, own_transaction = await self._get_connection(use_conn)
            # Start a transaction if not already in one
            if own_transaction:
                await conn.begin()
            # Connect to database and execute upsert
            await conn.execute(
                text(
                    f"SET timezone TO '{self._tz}'"
                )
            )
            result = await conn.execute(upsert_stmt)
            # Explicitly commit the transaction
            # Only commit if we started the transaction ourselves
            if own_transaction:
                await conn.commit()
            # Get the result information
            if result.returns_rows:
                # If the statement returns rows (like RETURNING clause), fetch them
                return result.fetchall()
            else:
                # For INSERT/UPDATE without RETURNING, get rowcount
                return {"rowcount": result.rowcount, "status": "success"}
        except (ProgrammingError, OperationalError) as err:
            # Only rollback if we started our own transaction
            if own_transaction and conn.in_transaction():
                await conn.rollback()
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
        finally:
            if own_transaction:
                await conn.close()

    async def upsert_many(
        self,
        objects: List[Union[Dict[str, Any], Any]],
        table_name: str,
        schema: str,
        primary_keys: Optional[List[str]] = None,
        constraint: Optional[str] = None,
        foreign_keys: Optional[Dict[str, Any]] = None,
        as_values: bool = True,
        batch_size: int = 100
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
        try:
            async with self._engine.begin() as conn:
                # Process objects in batches
                for i in range(0, len(objects), batch_size):
                    batch = objects[i:i + batch_size]
                    batch_results = []
                    for obj in batch:
                        result = await self.do_upsert(
                            obj=obj,
                            table_name=table_name,
                            schema=schema,
                            primary_keys=primary_keys,
                            constraint=constraint,
                            foreign_keys=foreign_keys,
                            as_values=as_values,
                            use_conn=conn
                        )
                        batch_results.append(result)
                    results.extend(batch_results)
                return results
        except Exception as err:
            raise ValueError(
                f"Error upserting objects: {err}"
            ) from err

    async def close(self):
        """Close the database engine."""
        try:
            if self._connection is not None:
                # if we are in a transaction, then commit:
                if self._connection.in_transaction():
                    await self._connection.commit()
                await self._connection.close()
            if self.use_async:
                await self._engine.dispose()
            else:
                self._engine.dispose()
        except Exception as err:
            self.logger.error(err)
            raise OutputError(
                f"Error closing database connection: {err}"
            ) from err
        finally:
            self._connection = None

    def write(
        self,
        table: str,
        schema: str,
        data: Union[List[Dict], Any],
        on_conflict: Optional[str] = 'replace',
        pk: List[str] = None
    ):
        raise NotImplementedError("Method not implemented")
