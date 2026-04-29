"""
querysource.auth — Policy-based access control (PBAC) for QuerySource.

This package wires navigator-auth's PBAC engine into QuerySource handlers
and provides a per-user credential resolver for the driver layer.

Public surface:
- ``CredentialResolver``  — per-user credential resolver (TASK-628)
- ``ResolvedCredentials`` — credential dataclass (TASK-628)
- ``setup_pbac()``        — PBAC bootstrap (TASK-629)
- ``ResourceType``        — resource-type enum/shim (TASK-631)
"""
import logging

logger = logging.getLogger(__name__)

from querysource.auth.credentials import CredentialResolver, ResolvedCredentials  # noqa: E402
from querysource.auth.pbac import setup_pbac  # noqa: E402

__all__ = ("CredentialResolver", "ResolvedCredentials", "setup_pbac", "logger")
