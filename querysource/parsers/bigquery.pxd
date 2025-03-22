from .abstract cimport AbstractParser
from .sql cimport SQLParser
from cpython.object cimport PyObject

cdef class BigQueryParser(SQLParser):
    # Declare class attributes
    cdef:
        public object _json_pattern

    # cpdef str query(self)
