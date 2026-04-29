"""
querysource.auth — Policy-based access control (PBAC) for QuerySource.

This package wires navigator-auth's PBAC engine into QuerySource handlers
and provides a per-user credential resolver for the driver layer.

Public surface (filled in by subsequent tasks):
- ``setup_pbac()``        — TASK-629
- ``CredentialResolver``  — TASK-628
"""
import logging

logger = logging.getLogger(__name__)
