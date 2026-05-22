"""AirtableSource — MultiQuery ThreadSource for Airtable tables.

Fetches all records from an Airtable base/table and returns them as a
pandas DataFrame.  Delegates all HTTP and pagination logic to
:class:`~querysource.interfaces.airtable.AirtableInterface`.

Auth precedence (evaluated at fetch-time):
1. Session OAuth2 token from ``navigator_session`` (per-user, interactive).
2. ``AIRTABLE_ACCESS_TOKEN`` env var resolved via navconfig (server-wide PAT).
3. :class:`RuntimeError` when neither is available.

Configuration dict shape::

    {
        "credentials": {
            # Optional explicit override; otherwise resolved from env.
            "access_token": "AIRTABLE_ACCESS_TOKEN",
            "client_id":    "AIRTABLE_CLIENT_ID",
            "client_secret":"AIRTABLE_CLIENT_SECRET",
        },
        "source": {
            # EITHER a full Airtable URL:
            "url": "https://airtable.com/appXXX/tblYYY/viwZZZ",
            # OR explicit identifiers:
            "base_id":  "appXXX",
            "table":    "tblYYY",     # accepts table_id or table_name
            "view":     "viwZZZ",     # optional

            # Optional API-side filters:
            "filter_by_formula": "AND(...)",
            "max_records": 5000,
            "page_size":  100,
        }
    }
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from aiohttp import web

from .base import ThreadSource
from ....interfaces.airtable import (
    AirtableInterface,
    AirtableReauthRequired,  # noqa: F401 — re-exported for caller convenience
    AirtableTokens,
    TokenPersistFn,
)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string to an aware UTC datetime.

    Args:
        value: ISO 8601 string (e.g. ``"2099-01-01T00:00:00+00:00"``),
            or ``None``.

    Returns:
        An aware :class:`datetime` in UTC, or ``None`` when *value* is falsy.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


class AirtableSource(ThreadSource):
    """Fetch all records from an Airtable table and return as a DataFrame.

    Thin :class:`~querysource.queries.multi.sources.base.ThreadSource`
    subclass.  All Airtable HTTP/pagination complexity lives in
    :class:`~querysource.interfaces.airtable.AirtableInterface`.

    Args:
        name: Source name (used as the queue key and for logging).
        options: Configuration dict (see module docstring for shape).
        request: Current aiohttp request (used to look up the user session).
        queue: Shared asyncio Queue — results are put here by
            :meth:`~querysource.queries.multi.sources.base.ThreadSource.run`.
    """

    BASE_URL: str = "https://api.airtable.com/v0"

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None:
        super().__init__(name, options, request, queue)
        self._logger = logging.getLogger(__name__)

        creds = options.get('credentials', {})
        source = options.get('source', {})

        # ── Resolve source identifiers ──────────────────────────────────────
        url = source.get('url')
        if url:
            self._base_id, self._table, self._view = AirtableInterface.parse_url(url)
        else:
            self._base_id = source.get('base_id') or self.resolve_credential(
                'base_id', 'AIRTABLE_BASE_ID'
            )
            self._table = source.get('table')
            self._view = source.get('view')

        # ── API-side filter knobs ────────────────────────────────────────────
        self._filter_by_formula: Optional[str] = source.get('filter_by_formula')
        self._max_records: Optional[int] = source.get('max_records')
        self._page_size: int = source.get('page_size', 100)

        # ── Store creds; PAT resolution deferred to fetch() ─────────────────
        self._creds = creds

    async def fetch(self) -> pd.DataFrame:
        """Fetch all records from Airtable and return as a DataFrame.

        Implements the auth precedence: session OAuth first, PAT fallback,
        raise on neither.

        Returns:
            A :class:`pandas.DataFrame` with one row per Airtable record
            (built from the ``fields`` dict of each record).  An empty table
            yields an empty — but valid — DataFrame, never ``None``.

        Raises:
            ValueError: When neither a URL nor explicit ``base_id``/``table``
                identifiers were supplied.
            RuntimeError: When no credentials are available.
            AirtableReauthRequired: When an OAuth refresh attempt fails; the
                caller should redirect the user to the ``/connect`` flow.
        """
        if not self._base_id or not self._table:
            raise ValueError(
                "AirtableSource: 'source.url' or 'source.base_id'+'source.table' is required."
            )

        # ── Auth selection ───────────────────────────────────────────────────
        session_tokens = await self._resolve_session_tokens()

        if session_tokens is not None:
            self._logger.debug(
                "AirtableSource %r: using session OAuth token.", self._name
            )
            client_id = self.resolve_credential(
                'client_id', self._creds.get('client_id', 'AIRTABLE_CLIENT_ID')
            )
            client_secret = self.resolve_credential(
                'client_secret', self._creds.get('client_secret', 'AIRTABLE_CLIENT_SECRET')
            )
            interface = AirtableInterface(
                tokens=session_tokens,
                is_oauth=True,
                client_id=client_id or None,
                client_secret=client_secret or None,
                persist_tokens=self._make_session_writeback(),
            )
        else:
            pat = self.resolve_credential(
                'access_token', self._creds.get('access_token', 'AIRTABLE_ACCESS_TOKEN')
            )
            if not pat or pat == 'AIRTABLE_ACCESS_TOKEN':
                raise RuntimeError(
                    "AirtableSource: no credentials available — provide a session OAuth token "
                    "or set AIRTABLE_ACCESS_TOKEN"
                )
            self._logger.info(
                "AirtableSource %r: using AIRTABLE_ACCESS_TOKEN (PAT).", self._name
            )
            interface = AirtableInterface(
                tokens=AirtableTokens(
                    access_token=pat,
                    refresh_token=None,
                    expires_at=None,
                    scope=None,
                ),
                is_oauth=False,
            )

        # ── Fetch records ────────────────────────────────────────────────────
        records = await interface.list_records(
            self._base_id,
            self._table,
            self._view,
            filter_by_formula=self._filter_by_formula,
            max_records=self._max_records,
            page_size=self._page_size,
        )

        # ── Build DataFrame — empty is fine, never None ──────────────────────
        df = pd.DataFrame.from_records([r.get("fields", {}) for r in records])
        df = df.infer_objects()
        return df

    # ── Private helpers ──────────────────────────────────────────────────────

    async def _resolve_session_tokens(self) -> Optional[AirtableTokens]:
        """Read Airtable OAuth tokens from the request session, if available.

        Lazy-imports ``navigator_session`` so this source works in deployments
        where that package is not installed (falls back to PAT silently).

        Returns:
            :class:`~querysource.interfaces.airtable.AirtableTokens` when
            the session contains a valid ``airtable`` bundle, else ``None``.
        """
        try:
            from navigator_session import get_session  # noqa: PLC0415
        except ImportError:
            self._logger.debug(
                "AirtableSource: navigator_session not installed; skipping session auth."
            )
            return None

        try:
            session = await get_session(self._request, new=False)
        except RuntimeError:
            self._logger.debug(
                "AirtableSource: get_session() raised RuntimeError; skipping session auth."
            )
            return None

        if session is None:
            return None

        bundle = session.get('airtable')
        if not bundle:
            return None

        return AirtableTokens(
            access_token=bundle['access_token'],
            refresh_token=bundle.get('refresh_token'),
            expires_at=_parse_dt(bundle.get('expires_at')),
            scope=bundle.get('scope'),
            token_type=bundle.get('token_type', 'Bearer'),
        )

    def _make_session_writeback(self) -> TokenPersistFn:
        """Return an async callback that persists refreshed tokens to the session.

        The returned coroutine closes over ``self._request`` only (not over any
        mutable AirtableSource state) so it is safe to invoke from inside
        :class:`~querysource.interfaces.airtable.AirtableInterface`.

        Returns:
            An ``async def`` callable matching :data:`~querysource.interfaces.airtable.TokenPersistFn`.
        """
        request = self._request
        logger = self._logger

        async def writeback(new_tokens: AirtableTokens) -> None:
            try:
                from navigator_session import get_session  # noqa: PLC0415
                session = await get_session(request, new=False)
                if session is None:
                    logger.warning(
                        "AirtableSource: session writeback skipped — no session found."
                    )
                    return
                session['airtable'] = {
                    "access_token": new_tokens.access_token,
                    "refresh_token": new_tokens.refresh_token,
                    "expires_at": (
                        new_tokens.expires_at.isoformat()
                        if new_tokens.expires_at else None
                    ),
                    "scope": new_tokens.scope,
                    "token_type": new_tokens.token_type,
                }
            except RuntimeError as exc:
                logger.warning(
                    "AirtableSource: session writeback failed (RuntimeError): %s", exc
                )

        return writeback
