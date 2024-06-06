# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=False, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
# mapping.pxd
cdef class ClassDict(dict):
    cdef dict mapping
    cdef list _columns
    cdef object default

    # cdef inline object __missing__(self, key):
    #     pass

    # cdef inline object __len__(self):
    #     pass

    # cdef inline object __str__(self):
    #     pass

    # cdef inline object __repr__(self):
    #     pass

    # cdef inline object __contains__(self, key):
    #     pass

    # cdef inline get(self, key, default=None):
    #     pass

    # cdef inline object __delitem__(self, key):
    #     pass

    # cdef inline object __setitem__(self, key, value):
    #     pass

    # cdef inline object __getitem__(self, key):
    #     pass

    # cdef inline list keys(self):
    #     pass

    # cdef inline list values(self):
    #     pass

    # cdef inline list items(self):
    #     pass

    # cdef inline pop(self, key, default=None):
    #     pass

    # cdef inline clear(self):
    #     pass

    # cdef inline object __iter__(self):
    #     pass

    # cdef inline object __getattr__(self, attr):
    #     pass

    # cdef inline object __delattr__(self, name):
    #     pass
