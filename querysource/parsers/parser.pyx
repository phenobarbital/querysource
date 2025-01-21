# cython: language_level=3, embedsignature=True
# Copyright (C) 2018-present Jesus Lara
#
# file: parser.pyx
"""
Base Query Parser.
"""
from abc import ABC, abstractmethod
import asyncio
from ..models import QueryObject
from ..exceptions cimport EmptySentence
from .abstract cimport AbstractParser


cdef class QueryParser(AbstractParser):
    """ Base Query Parser for All Queries. """
    def __init__(
        self,
        *args,
        **kwargs
    ):
        pass
