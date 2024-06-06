# file: abstractparser.pyx

from abc import abstractmethod
from ..models import QueryObject


cdef class AbstractParser:
    def __init__(self, str query, dict options, dict conditions):
        self.query_raw = query
        self.options = options
        self.set_conditions(conditions)

    cpdef void set_conditions(self, dict conditions):
        self.conditions = QueryObject(conditions)

    cpdef void parse_query(self):
        pass  # Implement parsing logic

    cpdef str build_query(self):
        pass
