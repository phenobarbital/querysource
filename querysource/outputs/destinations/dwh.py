"""
DWH (Data Warehouse) Destination.

Writes a pandas DataFrame to a managed data warehouse backend:
BigQuery, DocumentDB, or DynamoDB.

Each backend has different write semantics and is accessed via asyncdb drivers
or the existing engine classes in ``querysource.outputs.tables.TableOutput``.

YAML configuration example::

    Output:
      - DWH:
          driver: bigquery
          schema: analytics
          table: daily_metrics
          method: upsert
          pk:
            - date
            - store_id
          credentials:
            project_id: BIGQUERY_PROJECT_ID
            credentials: BIGQUERY_CREDENTIALS

Supported drivers: ``bigquery``, ``documentdb``, ``dynamodb``.
Supported methods: ``append``, ``upsert``, ``truncate``.

External dependencies:
- BigQuery: ``google-cloud-bigquery``, ``google-auth`` (via asyncdb bigquery driver)
- DocumentDB: ``motor`` (asyncdb mongo driver)
- DynamoDB: ``aiobotocore`` (asyncdb dynamodb driver)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
import pandas as pd
from querysource.exceptions import DriverError, OutputError
from .abstract import AbstractDestination


# ---------------------------------------------------------------------------
# Driver validation
# ---------------------------------------------------------------------------

_VALID_DWH_DRIVERS = frozenset({"bigquery", "documentdb", "dynamodb"})
_VALID_METHODS = frozenset({"append", "upsert", "truncate"})

# DynamoDB batch size (max 25 per batch_write_item request)
_DYNAMO_BATCH_SIZE = 25


class DWHDestination(AbstractDestination):
    """
    Write a DataFrame to a data warehouse backend.

    Unlike :class:`~querysource.outputs.destinations.table.TableDestination`
    (which targets relational databases), ``DWHDestination`` targets
    managed, non-relational, or cloud-native data stores.

    Accepted YAML configuration keys:

    * ``driver`` — one of ``bigquery``, ``documentdb``, ``dynamodb``
    * ``schema`` — BigQuery dataset / DocumentDB database / DynamoDB table prefix
    * ``table`` — table / collection / DynamoDB table name
    * ``method`` — ``append`` (default), ``upsert``, ``truncate``
    * ``pk`` — list of primary key columns (for upsert)
    * ``credentials`` — dict of connection credentials; values can be
      navconfig variable names (ALL_CAPS_SNAKE_CASE)
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        super().__init__(data, **kwargs)

        driver: str = kwargs.get("driver", "")
        if not driver or driver not in _VALID_DWH_DRIVERS:
            supported = ", ".join(sorted(_VALID_DWH_DRIVERS))
            raise OutputError(
                f"DWHDestination: unsupported driver '{driver}'. "
                f"Supported: {supported}"
            )
        self._driver: str = driver

        method: str = (kwargs.get("method", "append") or "append").lower()
        if method not in _VALID_METHODS:
            raise OutputError(
                f"DWHDestination: unsupported method '{method}'. "
                f"Supported: {', '.join(sorted(_VALID_METHODS))}"
            )
        self._method: str = method
        self._table: str = kwargs.get("table", "")
        self._schema: str = kwargs.get("schema", "")
        self._pk: List[str] = kwargs.get("pk", []) or []

        raw_creds: dict = kwargs.get("credentials", {}) or {}
        self._credentials: dict = self.resolve_credentials(raw_creds)

    # -----------------------------------------------------------------------
    # BigQuery write
    # -----------------------------------------------------------------------

    async def _write_bigquery(self, df: pd.DataFrame) -> None:
        """
        Write *df* to a BigQuery table.

        Uses the existing :class:`~querysource.outputs.tables.TableOutput.bigquery.BigQueryOutput`
        engine, which handles ``WRITE_APPEND``, ``WRITE_TRUNCATE``, and
        MERGE-based upsert via its ``db_upsert`` method.

        :param df: DataFrame to write.
        :raises OutputError: On write failure.
        """
        try:
            from querysource.outputs.tables.TableOutput.bigquery import BigQueryOutput
        except ImportError as exc:
            raise OutputError(
                "DWHDestination (bigquery): BigQueryOutput is not available. "
                "Ensure 'google-cloud-bigquery' is installed."
            ) from exc

        try:
            engine = BigQueryOutput(parent=self, external=True)
            on_conflict: str
            use_merge: Optional[bool]

            if self._method == "truncate":
                on_conflict = "replace"  # BigQuery WRITE_TRUNCATE
                use_merge = False
            elif self._method == "upsert":
                on_conflict = "upsert"
                use_merge = True
            else:
                on_conflict = "append"   # BigQuery WRITE_APPEND
                use_merge = False

            await engine.db_upsert(
                table=self._table,
                schema=self._schema,
                data=df,
                on_conflict=on_conflict,
                pk=self._pk or None,
                use_merge=use_merge,
            )
            self.logger.info(
                "DWHDestination (bigquery): wrote %d rows → %s.%s",
                len(df),
                self._schema,
                self._table,
            )
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"DWHDestination (bigquery): write failed: {err}"
            ) from err
        finally:
            try:
                await engine.close()
            except Exception:
                pass

    # Parent-protocol shims used by BigQueryOutput
    @property
    def tablename(self) -> str:
        return self._table

    @property
    def pk(self) -> List[str]:
        return self._pk

    @property
    def if_exists(self) -> str:
        return "upsert" if self._method == "upsert" else "append"

    @property
    def foreign_key(self):
        return None

    @property
    def constraint(self):
        return None

    def get_schema(self) -> str:
        return self._schema

    def primary_keys(self) -> list:
        return self._pk

    def constraints(self):
        return None

    def foreign_keys(self):
        return None

    # -----------------------------------------------------------------------
    # DocumentDB write
    # -----------------------------------------------------------------------

    async def _write_documentdb(self, df: pd.DataFrame) -> None:
        """
        Write *df* to a DocumentDB (MongoDB-compatible) collection.

        Converts the DataFrame to a list of dicts and performs:

        * ``append``   → ``insert_many``
        * ``upsert``   → ``update_one`` with ``$set`` / ``upsert=True`` per row
        * ``truncate`` → ``delete_many({})`` then ``insert_many``

        :param df: DataFrame to write.
        :raises OutputError: On write failure.
        """
        try:
            from asyncdb import AsyncDB
        except ImportError as exc:
            raise OutputError(
                "DWHDestination (documentdb): asyncdb is required. "
                "Install it with: pip install asyncdb"
            ) from exc

        creds = self._credentials
        params: Dict[str, Any] = {
            "host": creds.get("host", "localhost"),
            "port": int(creds.get("port", 27017)),
            "username": creds.get("username", ""),
            "password": creds.get("password", ""),
            "db": creds.get("database", self._schema) or self._schema,
        }
        if creds.get("ssl"):
            params["ssl"] = True
        if creds.get("tlsCAFile"):
            params["tlsCAFile"] = creds["tlsCAFile"]

        records = df.to_dict(orient="records")

        try:
            db = AsyncDB("mongo", params=params)
            async with await db.connection() as conn:
                collection = conn[params["db"]][self._table]

                if self._method == "truncate":
                    await collection.delete_many({})
                    self.logger.info(
                        "DWHDestination (documentdb): truncated collection %s",
                        self._table,
                    )
                    await collection.insert_many(records)
                elif self._method == "upsert" and self._pk:
                    for record in records:
                        pk_filter = {k: record[k] for k in self._pk if k in record}
                        await collection.update_one(
                            pk_filter,
                            {"$set": record},
                            upsert=True,
                        )
                else:
                    # append (or upsert without PK — fall back to insert)
                    await collection.insert_many(records)

                self.logger.info(
                    "DWHDestination (documentdb): wrote %d records → %s",
                    len(records),
                    self._table,
                )
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"DWHDestination (documentdb): write to {self._table} failed: {err}"
            ) from err

    # -----------------------------------------------------------------------
    # DynamoDB write
    # -----------------------------------------------------------------------

    async def _write_dynamodb(self, df: pd.DataFrame) -> None:
        """
        Write *df* to a DynamoDB table using batch ``put_item`` operations.

        DynamoDB ``put_item`` is inherently an upsert (full replacement of the
        item with the same primary key), so all write modes use it:

        * ``append`` / ``upsert`` → batch ``put_item``
        * ``truncate``            → scan + batch delete, then batch ``put_item``

        :param df: DataFrame to write.
        :raises OutputError: On write failure.
        """
        try:
            import aioboto3
        except ImportError as exc:
            raise OutputError(
                "DWHDestination (dynamodb): aioboto3 is required. "
                "Install it with: pip install aioboto3"
            ) from exc

        creds = self._credentials
        region = creds.get("region_name") or creds.get("region", "us-east-1")
        aws_key = creds.get("aws_key") or creds.get("access_key")
        aws_secret = creds.get("aws_secret") or creds.get("secret_key")

        session = aioboto3.Session(
            aws_access_key_id=aws_key or None,
            aws_secret_access_key=aws_secret or None,
            region_name=region,
        )

        records = df.to_dict(orient="records")
        # DynamoDB requires Decimal for floats — convert as needed
        cleaned_records = [_clean_dynamo_record(r) for r in records]

        try:
            async with session.resource("dynamodb") as dynamodb:
                table = dynamodb.Table(self._table)

                if self._method == "truncate":
                    await self._dynamo_truncate(table, self._pk)

                # Batch write in chunks of 25
                for i in range(0, len(cleaned_records), _DYNAMO_BATCH_SIZE):
                    batch = cleaned_records[i: i + _DYNAMO_BATCH_SIZE]
                    async with table.batch_writer() as writer:
                        for item in batch:
                            await writer.put_item(Item=item)

                self.logger.info(
                    "DWHDestination (dynamodb): wrote %d items → %s",
                    len(cleaned_records),
                    self._table,
                )
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"DWHDestination (dynamodb): write to {self._table} failed: {err}"
            ) from err

    @staticmethod
    async def _dynamo_truncate(table, pk_cols: List[str]) -> None:
        """Scan and delete all items from a DynamoDB table."""
        response = await table.scan()
        items = response.get("Items", [])
        while items:
            async with table.batch_writer() as writer:
                for item in items:
                    key = {k: item[k] for k in pk_cols if k in item}
                    if key:
                        await writer.delete_item(Key=key)
            # Handle pagination
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            response = await table.scan(ExclusiveStartKey=last_key)
            items = response.get("Items", [])

    # -----------------------------------------------------------------------
    # Central write dispatcher
    # -----------------------------------------------------------------------

    async def _write_to_dwh(self, df: pd.DataFrame) -> None:
        """
        Dispatch a single DataFrame write to the appropriate DWH backend.

        :param df: DataFrame to write.
        :raises OutputError: On write failure.
        """
        if self._driver == "bigquery":
            await self._write_bigquery(df)
        elif self._driver == "documentdb":
            await self._write_documentdb(df)
        elif self._driver == "dynamodb":
            await self._write_dynamodb(df)
        else:
            raise OutputError(
                f"DWHDestination: unknown driver '{self._driver}'"
            )

    # -----------------------------------------------------------------------
    # AbstractDestination interface
    # -----------------------------------------------------------------------

    async def run(self) -> Union[dict, pd.DataFrame]:
        """
        Write :attr:`data` to the configured DWH backend.

        Handles both a single :class:`~pandas.DataFrame` and a ``dict``
        of DataFrames.

        :returns: Original :attr:`data` (pass-through).
        :raises OutputError: On write failure.
        """
        try:
            if isinstance(self.data, dict):
                for key, df in self.data.items():
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        self.logger.info(
                            "DWHDestination: writing partition '%s' (%d rows)",
                            key,
                            len(df),
                        )
                        await self._write_to_dwh(df)
            elif isinstance(self.data, pd.DataFrame):
                if self.data.empty:
                    self.logger.warning(
                        "DWHDestination: DataFrame is empty, skipping write."
                    )
                else:
                    await self._write_to_dwh(self.data)
            else:
                raise DriverError(
                    f"DWHDestination: expected DataFrame or dict, got {type(self.data)}"
                )
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"DWHDestination: unexpected error: {err}"
            ) from err

        return self.data


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _clean_dynamo_record(record: dict) -> dict:
    """
    Convert float values in *record* to :class:`~decimal.Decimal` so they are
    accepted by DynamoDB.  DynamoDB's Python SDK rejects raw ``float`` values.

    NaN and None values are replaced with empty string ``""`` since DynamoDB
    cannot store NULL or NaN in item attributes.

    :param record: Dict record from ``DataFrame.to_dict(orient='records')``.
    :returns: Cleaned dict with floats converted to Decimal.
    """
    import math
    from decimal import Decimal
    cleaned: dict = {}
    for k, v in record.items():
        if v is None or (isinstance(v, float) and math.isnan(v)):
            # DynamoDB cannot store NULL or NaN
            cleaned[k] = ""
        elif isinstance(v, float):
            cleaned[k] = Decimal(str(v))
        else:
            cleaned[k] = v
    return cleaned
