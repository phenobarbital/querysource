# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
# file: obj.pyx
from typing import Optional, Any
from datamodel import Field
# from datamodel.libs.mapping import ClassDict
from querysource.types.mapping import ClassDict


cdef object to_field_list(object obj):
    if obj is None:
        return []
    if isinstance(obj, str):
        return [x.strip() for x in obj.split(',')]
    return obj

cdef object empty_dict(object value):
    if value is None:
        return {}
    return value


cdef class QueryObject(ClassDict):
    """Base Class for all options passed to Parsers.
    """
    source: Optional[str]
    driver: Optional[str]
    conditions: Optional[dict] = Field(default=empty_dict, default_factory=dict)
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
    querylimit: int
    query_raw: str
