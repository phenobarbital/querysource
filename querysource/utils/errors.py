"""querysource.utils.errors — Shared client-safe error-payload builder.

This module is the single place that decides what is and isn't exposed in
HTTP error responses.  Every error-producing site (AbstractHandler.Error /
.Except, DataOutput.error, AbstractWriter.error) routes through
``build_error_payload`` instead of building an inline ``reason``/``trace``
dict.

Public API
----------
- ``GENERIC_MESSAGES`` — category → safe public message mapping.
- ``build_error_payload(...)`` — generate a client-safe dict and log full
  detail server-side.
"""
import logging
import traceback
import uuid
from typing import Any, Optional

__all__ = ["GENERIC_MESSAGES", "build_error_payload"]

# ---------------------------------------------------------------------------
# Category → generic public message map (finalises spec §8 open question)
# ---------------------------------------------------------------------------

GENERIC_MESSAGES: dict[str, str] = {
    "bad_request": "Invalid query request",
    "query_error": "Query execution failed",
    "not_found": "Resource not found",
    "server_error": "Internal query error",
    "output_error": "Output generation failed",
}

# Fallback for unknown / missing categories
_DEFAULT_CATEGORY = "server_error"

# Module-level logger used when the caller does not supply one
_logger = logging.getLogger(__name__)


def build_error_payload(
    *,
    category: str,
    status: int,
    exception: Optional[BaseException] = None,
    debug: bool = False,
    logger: Optional[logging.Logger] = None,
    public_message: Optional[str] = None,
) -> dict[str, Any]:
    """Build a client-safe error payload.

    Always logs full detail (message + traceback) server-side under a
    generated ``error_id``.  Returns a minimal payload unless ``debug=True``,
    in which case the original detail and traceback are included.

    Args:
        category: Error category key (``"bad_request"``, ``"query_error"``,
            ``"not_found"``, ``"server_error"``, ``"output_error"``).
            Unknown keys fall back to ``"server_error"`` message.
        status: HTTP status code to embed in the payload.
        exception: The caught exception, if any.  Its string representation
            is captured as ``detail`` and included in the server log.
        debug: When ``True`` the returned dict includes ``"detail"`` and
            ``"trace"`` (development parity).  When ``False`` (production
            default) neither field appears.
        logger: Logger to use for the server-side log line.  Falls back to
            the module-level ``logging.getLogger(__name__)`` logger.
        public_message: If supplied, overrides the generic message from
            ``GENERIC_MESSAGES`` in the public ``"error"`` field.

    Returns:
        dict: Client-safe payload.  Production shape::

            {"error": str, "status": int, "error_id": str}

        Debug shape (superset)::

            {"error": str, "status": int, "error_id": str,
             "detail": str, "trace": str}
    """
    log = logger or _logger

    # 1. Generate a short, unique correlation ID
    error_id = uuid.uuid4().hex[:12]

    # 2. Capture the traceback string (always for the log; exposed only in debug mode)
    trace: str = traceback.format_exc(limit=20)
    # ``traceback.format_exc()`` returns "NoneType: None\n" when there is no
    # active exception — normalise to an empty string.
    if trace.strip() in ("NoneType: None", "None"):
        trace = ""

    # 3. Build the detail string (original exception message)
    detail: str = ""
    if exception is not None:
        detail = str(exception)

    # 4. Log the full detail server-side, tagged with error_id
    log.error(
        "[%s] category=%s status=%s detail=%r trace=%s",
        error_id,
        category,
        status,
        detail or "(no detail)",
        trace or "(no traceback)",
    )

    # 5. Resolve the public message (never expose raw DB / internal text)
    safe_message = (
        public_message
        if public_message is not None
        else GENERIC_MESSAGES.get(category, GENERIC_MESSAGES[_DEFAULT_CATEGORY])
    )

    # 6. Build and return the payload
    payload: dict = {
        "error": safe_message,
        "status": status,
        "error_id": error_id,
    }
    if debug:
        payload["detail"] = detail
        payload["trace"] = trace

    return payload
