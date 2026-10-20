"""Stable errors for explicit query ownership boundaries."""

from querysource.exceptions import QueryException

OWNERSHIP_STATUS = {
    "invalid_tenant": 400,
    "tenant_not_available": 404,
    "query_not_found": 404,
    "tenant_store_unavailable": 503,
    "tenant_write_forbidden": 403,
    "tenant_worker_unsupported": 502,
    # FEAT-176 synchronous single-definition move.
    "move_same_store": 400,
    "move_validation_failed": 400,
    "move_target_exists": 409,
}


class TenantError(QueryException):
    """Separate a machine-readable owner error from the existing numeric code."""

    def __init__(self, message: str, *, error_code: str, details: dict | None = None) -> None:
        """Build the error; ``details`` is machine-readable context (never credentials or DSNs)."""
        super().__init__(message, code=OWNERSHIP_STATUS[error_code])
        self.error_code = error_code
        self.details = details
