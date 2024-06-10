# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""
Base Query Parser.
"""
from abc import ABC, abstractmethod
import asyncio
from cpython cimport list, dict, tuple
from ..exceptions import EmptySentence
from ..models import QueryObject
from .abstract cimport AbstractParser


cdef class QueryParser(AbstractParser):
    """ Base Query Parser for All Queries. """
    def __init__(
        self,
        *args,
        **kwargs
    ):
        pass
