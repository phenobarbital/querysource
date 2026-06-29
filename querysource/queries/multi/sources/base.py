import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd
from aiohttp import web


class ThreadSource(threading.Thread, ABC):
    """Abstract base class for all MultiQuery source threads.

    Encapsulates the common boilerplate shared by all MultiQuery source
    threads: creating an asyncio event loop, managing exceptions, and putting
    results into the shared queue.

    Each subclass must implement ``fetch()`` which performs the actual data
    retrieval and returns a pandas DataFrame.
    """

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ) -> None:
        super().__init__()
        self._queue = queue
        self.exc: Optional[Exception] = None
        self._name = name
        self._options = options
        self._request = request
        # masks: {"{name}": [fn, {args}]} specs resolved at fetch time via
        # fnExecutor (flowtask-compatible). Popped so it never leaks downstream.
        self._masks: dict = (
            options.pop('masks', {}) or {}
        ) if isinstance(options, dict) else {}
        self.logger = logging.getLogger(__name__)

    def resolve_credential(self, key: str, value: str) -> str:
        """Resolve a credential value via navconfig or return the literal.

        If ``value`` looks like an environment variable name (all uppercase
        letters and underscores), this method attempts to look it up in
        navconfig settings.  If the lookup succeeds the resolved value is
        returned; otherwise the original *value* is returned as-is.

        Args:
            key: The credential key name (used only for debugging purposes).
            value: The credential value — either a literal or a navconfig
                variable name.

        Returns:
            The resolved credential value, or the literal value if no
            navconfig match was found.
        """
        if isinstance(value, str) and value.isupper() and '_' in value:
            try:
                from navconfig import config  # noqa: PLC0415
                resolved = config.get(value)
                if resolved:
                    return resolved
            except ImportError:
                pass
        return value

    def resolve_masks(self, text: str) -> str:
        """Resolve ``masks`` placeholders in a path or filename.

        Masks are declared in the source config under ``masks`` as
        ``{"{name}": [function, {args}]}`` and resolved at fetch time via
        :func:`querysource.utils.fn.fnExecutor` (same mechanism, and same
        function vocabulary, as flowtask). Any function from
        ``querysource.utils.functions`` is available — ``today``, ``yesterday``,
        ``fdom``, ``ldom``, ``fdow``, ``ldow``, ``previous_month``,
        ``last_year``, etc. A plain (non-list) value is used verbatim.

        Example::

            "masks": {"{filedate}": ["today", {"mask": "%Y%m%d"}]}
            # "extract_{filedate}.csv" -> "extract_20260629.csv"

        Non-string, token-free, or mask-less values are returned unchanged.

        Args:
            text: The path/filename, possibly containing ``{name}`` tokens.

        Returns:
            The string with every declared mask replaced.
        """
        if not isinstance(text, str) or '{' not in text or not self._masks:
            return text
        from ....utils.fn import fnExecutor  # noqa: PLC0415

        for name, spec in self._masks.items():
            if name in text:
                value = fnExecutor(spec) if isinstance(spec, list) else spec
                text = text.replace(name, str(value))
        return text

    @property
    def slug(self) -> str:
        """Return a slug-like identifier for this source.

        Defaults to ``self._name``.  Subclasses (e.g. :class:`ThreadQuery`)
        may override this to expose the query slug.  This property is used by
        :class:`~querysource.queries.multi.MultiQS` error-handling code.
        """
        return self._name

    @abstractmethod
    async def fetch(self) -> pd.DataFrame:
        """Fetch data and return as a DataFrame.

        This method is called from within a fresh asyncio event loop running
        in the thread.  Subclasses must implement it to perform the actual
        data retrieval.

        Returns:
            A pandas DataFrame containing the fetched data.

        Raises:
            Any exception that occurs during data retrieval.  The exception
            will be captured in ``self.exc`` by ``run()``.
        """

    def run(self) -> None:
        """Thread entry point.

        Creates a new asyncio event loop, calls ``fetch()``, and — if
        ``fetch()`` returns a non-``None`` value — puts the result into the
        shared queue.  Subclasses whose ``fetch()`` already writes to the
        queue internally (e.g. :class:`ThreadQuery`) can return ``None`` to
        skip the automatic queue-put step.

        Exceptions are captured in ``self.exc``; the event loop is always
        closed in the ``finally`` block.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(self.fetch())
            if result is not None:
                loop.run_until_complete(self._queue.put({self._name: result}))
        except Exception as ex:  # noqa: BLE001
            self.logger.error("ThreadSource %r failed: %s", self._name, ex, exc_info=True)
            self.exc = ex
        finally:
            try:
                loop.stop()
                loop.close()
            except Exception:  # noqa: BLE001
                pass
