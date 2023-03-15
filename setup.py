#!/usr/bin/env python
"""QuerySource.

    Aiohttp web service for querying several databases easily.
See:
https://github.com/phenobarbital/querysource/
"""
import ast
from os import path
from setuptools import find_packages, setup, Extension
from Cython.Build import cythonize

def get_path(filename):
    return path.join(path.dirname(path.abspath(__file__)), filename)


def readme():
    with open(get_path('README.md'), 'r', encoding='utf-8') as rd:
        return rd.read()


version = get_path('querysource/version.py')
with open(version, 'r', encoding='utf-8') as meta:
    t = compile(meta.read(), version, 'exec', ast.PyCF_ONLY_AST)
    for node in (n for n in t.body if isinstance(n, ast.Assign)):
        if len(node.targets) == 1:
            name = node.targets[0]
            if isinstance(name, ast.Name) and \
                    name.id in (
                        '__version__',
                        '__title__',
                        '__description__',
                        '__author__',
                        '__license__', '__author_email__'):
                v = node.value
                if name.id == '__version__':
                    __version__ = v.s
                if name.id == '__title__':
                    __title__ = v.s
                if name.id == '__description__':
                    __description__ = v.s
                if name.id == '__license__':
                    __license__ = v.s
                if name.id == '__author__':
                    __author__ = v.s
                if name.id == '__author_email__':
                    __author_email__ = v.s

COMPILE_ARGS = ["-O2"]

extensions = [
    Extension(
        name='querysource.exceptions',
        sources=['querysource/exceptions.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c"
    ),
    Extension(
        name='querysource.libs.json',
        sources=['querysource/libs/json.pyx'],
        extra_compile_args=COMPILE_ARGS,
        language="c++"
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
    name='querysource',
    version=__version__,
    python_requires=">=3.9.16",
    url='https://github.com/phenobarbital/querysource/',
    description=__description__,
    long_description=readme(),
    long_description_content_type='text/markdown',
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Application Frameworks",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Software Development :: Build Tools",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: BSD License",
    ],
    author='Jesus Lara',
    author_email='jesuslarag@gmail.com',
    packages=find_packages(exclude=['contrib', 'docs', 'tests']),
    include_package_data=True,
    package_data={"querysource": ["py.typed"]},
    license=__license__,
    license_files = 'LICENSE',
    setup_requires=[
        "wheel==0.38.4",
        "Cython==0.29.33",
        "asyncio==3.4.3",
    ],
    install_requires=[
        "aiodns==3.0.0",
        "asyncio==3.4.3",
        "uvloop==0.17.0",
        'LivePopularTimes==1.3',
        'hubspot-api-client==7.5.0',
        'oauth2client==4.1.3',
        'google-analytics-data==0.16.1',
        'google-api-python-client==2.80.0',
        'google-auth-oauthlib==1.0.0',
        'sqloxide==0.1.30',
        'aiocsv==1.2.3',
        'xlsxwriter==3.0.8',
        'odswriter==0.4.0',
        'odfpy==1.4.1',
        'xlrd==2.0.1',
        'pandas_bokeh==0.5.5',
        'plotly==5.13.0',
        'sweetviz==2.1.4',
        # 'great_expectations==0.15.48',
        'pygal==3.0.0',
        'reportlab==3.6.12',
        'WeasyPrint==58.1',
        'APScheduler==3.10.1',
        'proxylists>=0.10.2',
        'async-notify>=0.8.0',
        'navconfig[default]>=1.1.0',
        'asyncdb[all]>=2.2.0',
        'navigator-session>=0.3.3',
        'scikit-learn==1.2.1',
        'elasticsearch-async==6.2.0',
        'seaborn==0.12.2',
        # 'pandas_profiling==3.6.6',
        'simple_salesforce==1.12.3'
    ],
    tests_require=[
            'pytest>=5.4.0',
            'coverage',
            'pytest-asyncio',
            'pytest-xdist',
            'pytest-assume'
    ],
    ext_modules=cythonize(extensions),
    project_urls={  # Optional
        'Source': 'https://github.com/phenobarbital/querysource/',
        'Funding': 'https://paypal.me/phenobarbital',
        'Say Thanks!': 'https://saythanks.io/to/phenobarbital',
    },
)
