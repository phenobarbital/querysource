"""
TableDeleteDestination.

MultiQuery Output step ``TableDelete``: delete the rows of ``schema.table``
whose primary-key tuple appears in the pipeline DataFrame (PostgreSQL only).
Uses the full-access ``DB*`` DSN and runs in a single transaction.
"""
import re
import uuid
from typing import Union

import pandas as pd
from asyncdb import AsyncDB

from querysource.conf import default_dsn
from querysource.exceptions import DataNotFound, OutputError
from querysource.outputs.destinations.abstract import AbstractDestination

IDENTIFIER_RE: re.Pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Driver exception class names meaning "the target table/column/schema is
# wrong" (a config/data problem, HTTP 422) rather than an infra failure.
_DATA_ERROR_NAMES = frozenset({
    "UndefinedTableError", "UndefinedColumnError", "InvalidSchemaNameError",
    "DataError", "IntegrityError",
})

_PK_TYPES_SQL = (
    "SELECT a.attname, format_type(a.atttypid, a.atttypmod) "
    "FROM pg_attribute a "
    "WHERE a.attrelid = $1::regclass AND a.attname = ANY($2) "
    "AND NOT a.attisdropped"
)


class TableDeleteDestination(AbstractDestination):
    """Delete rows of ``schema.table`` whose ``pk`` tuple is present in the pipeline data.

    Step name: ``TableDelete``. PostgreSQL only. Returns the input data unchanged.
    """

    _catalog = {
        "display_name": "TableDelete",
        "description": (
            "Delete rows from a PostgreSQL table whose primary-key values "
            "appear in the pipeline DataFrame."
        ),
        "usage": (
            "Place before a ``Table`` step (``method: append``) to refresh a "
            "slice of a table: ``TableDelete`` removes every target row whose "
            "``pk`` tuple is in the data, then ``Table`` re-inserts them. Rows "
            "with a NULL key are skipped. Not atomic across steps: the delete "
            "commits before the next step runs, so if ``Table`` fails re-run "
            "the pipeline (delete-then-append is idempotent). Rows missing "
            "from the source are not deleted; use ``ExecuteSQL`` for range deletes."
        ),
        "icon": "trash",
        "attributes": [
            {"name": "schema", "type": "str", "required": False, "default": "public",
             "description": "Target schema."},
            {"name": "table", "type": "str", "required": True, "default": "",
             "description": "Target table name."},
            {"name": "pk", "type": "list", "required": True, "default": [],
             "description": "Key column names matched against the DataFrame (composite allowed)."},
        ],
        "json_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "TableDelete",
            "description": "Delete rows of a table matching the DataFrame's key tuples.",
            "properties": {
                "schema": {"type": "string", "default": "public"},
                "table": {"type": "string"},
                "pk": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["table", "pk"],
            "additionalProperties": False,
        },
        "example": (
            '{\n  "Output": [\n'
            '    {"TableDelete": {"schema": "wm_assembly", "table": "employee_detail_profile",\n'
            '                     "pk": ["associate_id", "activity_date"]}},\n'
            '    {"Table": {"schema": "wm_assembly", "table": "employee_detail_profile",\n'
            '               "method": "append"}}\n'
            '  ]\n}'
        ),
    }

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        """Read ``schema`` (default ``public``), ``table`` (required), ``pk`` (required, non-empty list).

        Raises:
            OutputError: missing ``table``/``pk`` or an identifier failing ``IDENTIFIER_RE``.
        """
        super().__init__(data, **kwargs)
        self._schema: str = kwargs.get("schema") or "public"
        self._table: str = kwargs.get("table") or ""
        pk = kwargs.get("pk") or []
        self._pk: list[str] = [pk] if isinstance(pk, str) else list(pk)
        self.deleted_rows: int = 0
        if not self._table:
            raise OutputError("TableDelete: 'table' is required.", category="data")
        if not self._pk:
            raise OutputError("TableDelete: 'pk' must be a non-empty list.", category="data")
        for name in (self._schema, self._table, *self._pk):
            if not isinstance(name, str) or not IDENTIFIER_RE.fullmatch(name):
                raise OutputError(
                    f"TableDelete: invalid identifier {name!r}.", category="data"
                )

    @staticmethod
    def _quote(identifier: str) -> str:
        """Return ``identifier`` double-quoted (already validated by ``IDENTIFIER_RE``)."""
        return f'"{identifier}"'

    def _key_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return ``df[pk]`` without NULL-key rows and duplicates.

        Raises:
            OutputError: a ``pk`` column is absent from ``df`` (category ``data``).
        """
        missing = [col for col in self._pk if col not in df.columns]
        if missing:
            raise OutputError(
                f"TableDelete: pk column(s) not in data: {', '.join(missing)}",
                category="data",
            )
        keys = df[self._pk].dropna()
        dropped = len(df) - len(keys)
        if dropped > 0:
            self.logger.info("TableDelete: skipped %s row(s) with NULL key", dropped)
        return keys.drop_duplicates()

    @staticmethod
    def _to_native(value: object) -> object:
        """Convert a pandas/numpy scalar to a Python native accepted by asyncpg COPY."""
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if hasattr(value, "item"):
            return value.item()
        return value

    async def _delete_keys(self, keys: pd.DataFrame) -> int:
        """Stage ``keys`` in a temp table and run ``DELETE … USING`` in one transaction.

        Returns:
            Number of deleted rows.
        Raises:
            OutputError: table/column not found, or any database error (rolled back).
        """
        target = f"{self._quote(self._schema)}.{self._quote(self._table)}"
        tmp = f"_qs_del_{uuid.uuid4().hex[:8]}"
        records = [
            tuple(self._to_native(v) for v in row)
            for row in keys.itertuples(index=False, name=None)
        ]
        db = AsyncDB("pg", dsn=default_dsn)
        try:
            async with await db.connection() as conn:
                raw = conn.engine()
                async with raw.transaction():
                    rows = await raw.fetch(_PK_TYPES_SQL, target, self._pk)
                    types = {r[0]: r[1] for r in rows}
                    absent = [c for c in self._pk if c not in types]
                    if absent:
                        raise OutputError(
                            f"TableDelete: column(s) not found in {self._schema}."
                            f"{self._table}: {', '.join(absent)}",
                            category="data",
                        )
                    cols = ", ".join(f"{self._quote(c)} {types[c]}" for c in self._pk)
                    await raw.execute(
                        f"CREATE TEMP TABLE {self._quote(tmp)} ({cols}) ON COMMIT DROP"
                    )
                    await raw.copy_records_to_table(tmp, records=records, columns=self._pk)
                    match = " AND ".join(
                        f"t.{self._quote(c)} = d.{self._quote(c)}" for c in self._pk
                    )
                    status = await raw.execute(
                        f"DELETE FROM {target} t USING {self._quote(tmp)} d WHERE {match}"
                    )
        except OutputError:
            raise
        except Exception as err:
            category = "data" if type(err).__name__ in _DATA_ERROR_NAMES else "infra"
            self.logger.error(
                "TableDelete failed on %s.%s: %s", self._schema, self._table, err
            )
            raise OutputError(f"TableDelete: {err}", category=category) from err
        try:
            deleted = int(str(status).split()[-1])
        except (ValueError, IndexError):
            self.logger.warning("TableDelete: unparseable status %r", status)
            deleted = 0
        return deleted

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Apply the delete for a DataFrame or each DataFrame of a dict; set ``deleted_rows``.

        Returns:
            ``self.data`` unchanged.
        Raises:
            DataNotFound: data is empty / every frame is empty.
            OutputError: on validation or database failure.
        """
        frames: list[pd.DataFrame]
        if isinstance(self.data, dict):
            frames = [
                df for df in self.data.values()
                if isinstance(df, pd.DataFrame) and not df.empty
            ]
            if not frames:
                raise DataNotFound("TableDelete: all DataFrames in dict are empty.")
        elif isinstance(self.data, pd.DataFrame):
            if self.data.empty:
                raise DataNotFound("TableDelete: DataFrame is empty.")
            frames = [self.data]
        else:
            raise OutputError(
                f"TableDelete: expected DataFrame or dict, got {type(self.data)}",
                category="data",
            )
        total = 0
        for df in frames:
            keys = self._key_frame(df)
            if keys.empty:
                self.logger.info(
                    "TableDelete: no non-NULL keys for %s.%s", self._schema, self._table
                )
                continue
            total += await self._delete_keys(keys)
        self.deleted_rows = total
        self.logger.info(
            "TableDelete: deleted %s row(s) from %s.%s", total, self._schema, self._table
        )
        return self.data
