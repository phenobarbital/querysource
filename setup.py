#!/usr/bin/env python
"""QuerySource.

    Aiohttp web service for querying several databases easily.
See:
https://github.com/phenobarbital/querysource/
"""
import ast
from os import path
from setuptools import setup, Extension
from Cython.Build import cythonize

def get_path(filename):
    return path.join(path.dirname(path.abspath(__file__)), filename)

def readme():
    with open(get_path('README.md'), 'r', encoding='utf-8') as rd:
        return rd.read()

# Try to get version from setuptools_scm first, fall back to manual parsing
try:
    from setuptools_scm import get_version
    __version__ = get_version()
except Exception:
    version = get_path('querysource/version.py')
    with open(version, 'r', encoding='utf-8') as meta:
        t = compile(meta.read(), version, 'exec', ast.PyCF_ONLY_AST)
        for node in (n for n in t.body if isinstance(n, ast.Assign)):
            if len(node.targets) == 1:
                name = node.targets[0]
                if isinstance(name, ast.Name) and name.id == '__version__':
                    __version__ = node.value.s
                    break

COMPILE_ARGS = ["-O3"]

extensions = [
    Extension(
        name='querysource.exceptions',
        sources=['querysource/exceptions.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.abstract',
        sources=['querysource/parsers/abstract.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.parser',
        sources=['querysource/parsers/parser.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.sql',
        sources=['querysource/parsers/sql.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.pgsql',
        sources=['querysource/parsers/pgsql.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.sqlserver',
        sources=['querysource/parsers/sqlserver.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.sosql',
        sources=['querysource/parsers/sosql.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.bigquery',
        sources=['querysource/parsers/bigquery.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.cql',
        sources=['querysource/parsers/cql.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.influx',
        sources=['querysource/parsers/influx.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.parsers.mongo',
        sources=['querysource/parsers/mongo.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.utils.parseqs',
        sources=['querysource/utils/parseqs.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c++"
    ),
    Extension(
        name='querysource.types.typedefs',
        sources=['querysource/types/typedefs.pyx'],
        extra_compile_args=COMPILE_ARGS,
    ),
    Extension(
        name='querysource.types.validators',
        sources=['querysource/types/validators.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c++"
    ),
    Extension(
        name='querysource.types.converters',
        sources=['querysource/types/converters.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c++"
    ),
    Extension(
        name='querysource.utils.functions',
        sources=['querysource/utils/functions.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c++"
    )
]

setup(
    ext_modules=cythonize(extensions),
    zip_safe=False,
)
