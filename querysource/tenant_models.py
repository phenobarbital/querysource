from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TenantQueryDefinition(BaseModel):
    query_slug: str = Field(required=True)
    description: str | None = Field(required=False, default=None)
    source: str | None = Field(required=False, default=None)
    params: dict | None = Field(required=False, default=None)
    attributes: dict | None = Field(required=False, default=None)
    conditions: dict | None = Field(required=False, default=None)
    cond_definition: dict | None = Field(required=False, default=None)
    fields: list[str] | None = Field(required=False, default=None)
    filtering: dict | None = Field(required=False, default=None)
    ordering: list[str] | None = Field(required=False, default=None)
    grouping: list[str] | None = Field(required=False, default=None)
    qry_options: dict | None = Field(required=False, default=None)
    h_filtering: bool = Field(required=False, default=False)
    query_raw: str | None = Field(required=False, default=None)
    is_raw: bool = Field(required=False, default=False)
    is_cached: bool = Field(required=False, default=True)
    provider: str = Field(required=False, default='db')
    parser: str = Field(required=False, default='SQLParser')
    cache_timeout: int = Field(required=True, default=3600)
    cache_refresh: int = Field(required=True, default=0)
    cache_options: dict | None = Field(required=False, default=None)
    program_id: int = Field(required=True, default=1)
    dwh: bool = Field(required=True, default=False)
    dwh_driver: str | None = Field(required=False, default=None)
    dwh_info: dict | None = Field(required=False, default=None)
    dwh_scheduler: dict | None = Field(required=False, default=None)
    created_at: datetime | None = Field(required=False, default=None)
    created_by: int | None = Field(required=False, default=None)
    updated_at: datetime | None = Field(required=False, default=None)
    updated_by: int | None = Field(required=False, default=None)
