"""Models.

Models for querysource structure.
"""
from typing import List, Optional
from datetime import datetime
from asyncdb.models import Model, Field
from datamodel.libs.mapping import ClassDict
from .utils.functions import empty_dict
from .conf import (
    QS_QUERIES_SCHEMA,
    QS_QUERIES_TABLE
)

def rigth_now(obj) -> datetime:
    return datetime.now()

def to_field_list(obj) -> list:
    if obj is None:
        return []
    return [x.strip() for x in obj.split(',')] if isinstance(obj, str) else obj


class QueryObject(ClassDict):
    """Base Class for all options passed to Parsers.
    """
    source: Optional[str]
    driver: Optional[str]
    conditions: Optional[dict] = Field(default=empty_dict)
    coldef: Optional[dict]
    fields: list = Field(default=to_field_list, default_factory=list)
    ordering: Optional[list]
    group_by: Optional[list]
    qry_options: Optional[dict]
    ## filter
    filter: Optional[dict]
    where_cond: Optional[dict]
    and_cond: Optional[dict]
    hierarchy: Optional[list]
    # Limiting Query:
    querylimit: Optional[int]
    _limit: Optional[int]
    _offset: Optional[int]
    # Query Information:
    query_raw: str


class QueryModel(Model):
    query_slug: str = Field(required=True, primary_key=True)
    description: str = Field(required=False, default=None)
    # Source and primary attributes:
    source: Optional[str] = Field(required=False)
    params: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    attributes: Optional[dict] = Field(
        required=False,
        db_type='jsonb',
        default_factory=dict,
        comment="Optional Attributes for Query"
    )
    #  main conditions
    conditions: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    cond_definition: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    ## filter and grouping options
    fields: List[str] = Field(required=False, db_type='array', default_factory=list)
    filtering: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    ordering: List[str] = Field(required=False, db_type='array', default_factory=list)
    grouping: List[str] = Field(required=False, db_type='array', default_factory=list)
    qry_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    h_filtering: bool = Field(required=False, default=False, comment="filtering based on Hierarchical rules.")
    ### Query Information:
    query_raw: str = Field(required=False)
    is_raw: bool = Field(required=False, default=False)
    is_cached: bool = Field(required=False, default=True)
    provider: str = Field(required=False, default='db')
    parser: str = Field(required=False, default='SQLParser', comment="Parser to be used for parsing Query.")
    cache_timeout: int = Field(required=True, default=3600)
    cache_refresh: int = Field(required=True, default=0)
    cache_options: Optional[dict] = Field(required=False, db_type='jsonb', default_factory=dict)
    ## Program Information:
    program_id: int = Field(required=True, default=1)
    program_slug: str = Field(required=True, default='default')
    # DWH information
    dwh: bool = Field(required=True, default=False)
    dwh_driver: str = Field(required=False, default=None)
    dwh_info: Optional[dict] = Field(required=False, db_type='jsonb')
    dwh_scheduler: Optional[dict] = Field(required=False, db_type='jsonb')
    # Creation Information:
    created_at: datetime = Field(
        required=False,
        default=datetime.now,
        db_default='now()'
    )
    created_by: int = Field(required=False)  # TODO: validation for valid user
    updated_at: datetime = Field(
        required=False,
        default=datetime.now,
        encoder=rigth_now
    )
    updated_by: int = Field(required=False)  # TODO: validation for valid user

    class Meta:
        driver = 'pg'
        name = QS_QUERIES_TABLE
        schema = QS_QUERIES_SCHEMA
        strict = True
        frozen = False
        remove_nulls = True  # Auto-remove nullable (with null value) fields
