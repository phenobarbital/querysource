# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
# mapping.pxd
cdef class ClassDict(dict):
    cdef dict mapping
    cdef list _columns
    cdef object default
