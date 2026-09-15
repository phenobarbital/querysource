"""Stable errors for explicit query ownership boundaries."""

from querysource.exceptions import QueryException

OWNERSHIP_STATUS = {
    "invalid_tenant": 400,
    "tenant_not_available": 404,
    "query_not_found": 404,
    "tenant_store_unavailable": 503,
    "tenant_write_forbidden": 403,
    "tenant_worker_unsupported": 502,
}


class TenantError(QueryException):
    """Separate a machine-readable owner error from the existing numeric code."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message, code=OWNERSHIP_STATUS[error_code])
        self.error_code = error_code
