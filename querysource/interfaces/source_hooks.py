"""Pre/post SQL hooks for MultiQuery sources (FEAT-157).

Hooks are declared only on a MultiQuery ``queries`` entry (``pre-hook`` /
``post-hook``), guarded by the FEAT-156 Rust SQL guard and executed isolated on
the ``DB*`` credentials by :func:`execute_guarded` — never on the retrieval
connection, never in a transaction shared with it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar, List, Optional, Tuple, Union

from querysource.interfaces.guarded_sql import (  # FEAT-156
    GuardedSQLError,
    execute_guarded,
    guard_statements,
)

__all__ = (
    "HOOK_KEYS",
    "GuardedSQLError",
    "SourceHooks",
    "SourceHooksMixin",
    "build_hooks",
    "pop_hooks",
)

_log = logging.getLogger(__name__)

HOOK_KEYS: Tuple[str, str] = ("pre-hook", "post-hook")

RawHook = Optional[Union[str, List[str]]]


@dataclass(frozen=True)
class SourceHooks:
    """Guard-approved hook statements of one source."""

    pre: Tuple[str, ...] = ()
    post: Tuple[str, ...] = ()


def pop_hooks(entry: dict) -> Tuple[RawHook, RawHook]:
    """Remove and return the raw ``pre-hook`` / ``post-hook`` values of ``entry``.

    Args:
        entry: A MultiQuery ``queries`` entry. It is mutated: both keys are removed.

    Returns:
        ``(pre, post)``; each is ``None`` when the key was absent.
    """
    return entry.pop(HOOK_KEYS[0], None), entry.pop(HOOK_KEYS[1], None)


def build_hooks(pre: RawHook, post: RawHook) -> Optional[SourceHooks]:
    """Guard both raw values with ``guard_statements``.

    Args:
        pre: Raw ``pre-hook`` value (str, list of str, or None).
        post: Raw ``post-hook`` value (str, list of str, or None).

    Returns:
        ``None`` when both are absent, else the approved :class:`SourceHooks`.

    Raises:
        GuardedSQLError: blocked/unparsable/empty SQL or Rust extension missing.
    """
    if pre is None and post is None:
        return None
    pre_stmts = tuple(guard_statements(pre)) if pre is not None else ()
    post_stmts = tuple(guard_statements(post)) if post is not None else ()
    return SourceHooks(pre=pre_stmts, post=post_stmts)


class SourceHooksMixin:
    """Pre/post SQL hooks capability shared by ``BaseProvider`` and ``ThreadSource``.

    Hooks execute isolated on ``DB*`` credentials (``execute_guarded``), never
    on the retrieval connection. Defines no ``__init__`` (cooperative MRO).
    """

    #: ``"postgres"`` on providers whose sources may declare hooks; ``None`` elsewhere.
    sql_hooks_dialect: ClassVar[Optional[str]] = None
    _source_hooks: Optional[SourceHooks] = None

    def set_hooks(self, hooks: Optional[SourceHooks]) -> None:
        """Attach validated hooks (``None`` clears them)."""
        self._source_hooks = hooks

    @property
    def has_hooks(self) -> bool:
        """True when a pre- or post-hook is attached."""
        hooks = self._source_hooks
        return hooks is not None and bool(hooks.pre or hooks.post)

    async def run_pre_hook(self) -> List[str]:
        """Execute the pre-hook statements in one transaction.

        Returns:
            The status tags (``[]`` when no pre-hook is attached).

        Raises:
            GuardedSQLError: any connection/statement failure (rolled back).
        """
        hooks = self._source_hooks
        if hooks is None or not hooks.pre:
            return []
        _log.debug("running %d pre-hook statement(s)", len(hooks.pre))
        return await execute_guarded(list(hooks.pre))

    async def run_post_hook(self) -> List[str]:
        """Execute the post-hook statements in one transaction.

        Returns:
            The status tags (``[]`` when no post-hook is attached).

        Raises:
            GuardedSQLError: any connection/statement failure (rolled back).
        """
        hooks = self._source_hooks
        if hooks is None or not hooks.post:
            return []
        _log.debug("running %d post-hook statement(s)", len(hooks.post))
        return await execute_guarded(list(hooks.post))
