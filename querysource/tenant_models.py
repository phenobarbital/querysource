"""Tenant persistence definition: an independent plain datamodel that keeps
tenant persistence separate from the unchanged legacy QueryModel ORM shape.

Note: this module intentionally does NOT use
``from __future__ import annotations`` (PEP 563), and uses
``typing.Optional``/``typing.List`` rather than ``X | None``/``list[X]``
union syntax. This project's Cython ``datamodel`` validator resolves field
types at runtime from the live type objects: with postponed evaluation the
annotations become plain strings, and with ``X | None`` unions combined
with a non-trivial type (e.g. ``dict | None``) construction fails with
``TypeError: Expected type, got types.UnionType``. Both failure modes were
verified directly against the installed ``datamodel`` package in this
worktree. ``querysource/models.py`` (``QueryModel``) follows the same
``typing.Optional``/``typing.List`` convention.
"""
from datetime import datetime
from typing import List, Optional

from datamodel import BaseModel, Field

from querysource.models import rigth_now


class TenantQueryDefinition(BaseModel):
    """Persistence-validated tenant query definition.

    Declares every ``QueryModel`` field except ``program_slug`` — tenant
    schema selection is structural (the physical store), never a model
    field — with the exact defaults, validation and JSON/array metadata
    of the legacy model. ``datamodel.BaseModel`` rejects unexpected
    keyword arguments (``TypeError``) by construction, so passing
    ``program_slug`` here is rejected before model construction rather
    than silently ignored.
    """
    query_slug: str = Field(required=True, primary_key=True)
    description: str = Field(required=False, default=None)
    source: Optional[str] = Field(required=False)
    params: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    attributes: Optional[dict] = Field(
        required=False,
        db_type='jsonb',
        default_factory=dict,
        comment='Optional Attributes for Query',
    )
    conditions: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    cond_definition: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    fields: List[str] = Field(required=False, db_type='array', default_factory=list)
    filtering: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    ordering: List[str] = Field(required=False, db_type='array', default_factory=list)
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)
    columns_definition: List[str] = Field(
        required=False,
        db_type='array',
        default_factory=list,
        comment='Declared output columns of a multi-query definition (HEAD/PATCH inspection).',
    )
    qry_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    h_filtering: bool = Field(required=False, default=False, comment='filtering based on Hierarchical rules.')
    query_raw: str = Field(required=False)
    is_raw: bool = Field(required=False, default=False)
    is_cached: bool = Field(required=False, default=True)
    provider: str = Field(required=False, default='db')
    parser: str = Field(required=False, default='SQLParser', comment='Parser to be used for parsing Query.')
    cache_timeout: int = Field(required=True, default=3600)
    cache_refresh: int = Field(required=True, default=0)
    cache_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    program_id: int = Field(required=True, default=1)
    dwh: bool = Field(required=True, default=False)
    dwh_driver: str = Field(required=False, default=None)
    dwh_info: Optional[dict] = Field(required=False, db_type='jsonb')
    dwh_scheduler: Optional[dict] = Field(required=False, db_type='jsonb')
    created_at: datetime = Field(required=False, default=datetime.now, db_default='now()')
    created_by: int = Field(required=False)
    updated_at: datetime = Field(required=False, default=datetime.now, encoder=rigth_now)
    updated_by: int = Field(required=False)
