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
        name='querysource.types.mapping',
        sources=['querysource/types/mapping.pyx'],
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
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: BSD License",
    ],
    author='Jesus Lara',
    author_email='jesuslarag@gmail.com',
    packages=find_packages(
        exclude=[
            'contrib',
            'google',
            'docs',
            'plugins',
            'lab',
            'examples',
            'samples',
            'settings',
            'etc',
            'bin',
            'build'
        ]
    ),
    include_package_data=True,
    package_data={"querysource": ["py.typed"]},
    license=__license__,
    license_files='LICENSE',
    setup_requires=[
        "wheel==0.42.0",
        "Cython==3.0.6",
        "asyncio==3.4.3",
    ],
    install_requires=[
        "aiodns==3.0.0",
        'LivePopularTimes==1.3',
        'httpx==0.26.0',
        'hubspot-api-client==7.5.0',
        'oauth2client==4.1.3',
        'google-analytics-data==0.16.2',
        'google-api-python-client==2.86.0',
        'google-auth-oauthlib==1.0.0',
        'sqloxide==0.1.39',
        'aiocsv==1.3.2',
        'lxml==4.9.3',
        'xlsxwriter==3.2.0',
        'odswriter==0.4.0',
        'odfpy==1.4.1',
        'xlrd==2.0.1',
        'reportlab==4.1.0',
        'WeasyPrint==61.2',
        'APScheduler==3.10.4',
        'elasticsearch-async==6.2.0',
        'seaborn==0.13.2',
        'bs4==0.0.1',
        'simple_salesforce==1.12.3',
        'psycopg2-binary==2.9.9',
        'sqlalchemy==2.0.23',
        # NAV libraries:
        'asyncdb[all]>=2.6.0',
        'proxylists>=0.12.3',
        'async-notify>=1.2.1',
        'navconfig[default]>=1.7.0',
        'jsonschema==4.22.0',
        # Jinja2 extensions:
        "jinja2-iso8601==1.0.0",
        "jinja2-time==0.2.0",
        "jinja2-humanize-extension==0.4.0"
    ],
    extras_require={
        "analytics": [
            "great_expectations>=0.18.15",
            "pygwalker>=0.4.8.9",
            "ydata-profiling>=4.8.3",
            "sweetviz>=2.1.4",
            "pandas-eda>=1.2.0",
            'statsmodels==0.14.2',
            'pmdarima==2.0.4',
            'scikit-learn==1.4.2',
            'scpy==1.1.4',
            'pandas_bokeh==0.5.5',
            'plotly==5.22.0',
            'pygal==3.0.0',
            'keras-cv==0.9.0',
            'keras==3.4.1',
            'tiktoken==0.6.0',
            'yfinance==0.2.40',
            'safetensors==0.4.2',
            'selenium==4.18.1',
            'sentence-transformers==2.6.1',
            'tensorflow==2.17.0',
            'spacy==3.7.5'
        ]
    },
    tests_require=[
        'pytest>=5.4.0',
        'coverage',
        'pytest-asyncio',
        'pytest-xdist',
        'pytest-assume'
    ],
    ext_modules=cythonize(extensions),
    zip_safe=False,
    entry_points={
        'console_scripts': [
            'query = querysource.__cli__:main',
        ],
    },
    project_urls={  # Optional
        'Source': 'https://github.com/phenobarbital/querysource/',
        'Funding': 'https://paypal.me/phenobarbital',
        'Say Thanks!': 'https://saythanks.io/to/phenobarbital',
    },
)
