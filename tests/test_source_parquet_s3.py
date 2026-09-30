"""Unit tests for ParquetS3Source (FEAT-158, TASK-799). No real AWS access."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fsspec.implementations.memory import MemoryFileSystem


def _load_module(name: str, path: Path):
    """Load a source module without importing the Cython-dependent queries package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_ROOT = Path(__file__).parents[1]
for _name, _path in (
    ('querysource.queries', _ROOT / 'querysource' / 'queries'),
    ('querysource.queries.multi', _ROOT / 'querysource' / 'queries' / 'multi'),
    ('querysource.queries.multi.sources', _ROOT / 'querysource' / 'queries' / 'multi' / 'sources'),
    ('querysource.queries.multi.sources.parquet', _ROOT / 'querysource' / 'queries' / 'multi' / 'sources' / 'parquet'),
):
    _package = types.ModuleType(_name)
    _package.__path__ = [str(_path)]
    sys.modules[_name] = _package

_load_module(
    'querysource.queries.multi.sources.base', _ROOT / 'querysource' / 'queries' / 'multi' / 'sources' / 'base.py'
)
_load_module(
    'querysource.queries.multi.sources.parquet.filters',
    _ROOT / 'querysource' / 'queries' / 'multi' / 'sources' / 'parquet' / 'filters.py',
)
_load_module(
    'querysource.queries.multi.sources.parquet.base',
    _ROOT / 'querysource' / 'queries' / 'multi' / 'sources' / 'parquet' / 'base.py',
).ParquetSource
ParquetS3Source = _load_module(
    'querysource.queries.multi.sources.parquet.s3',
    _ROOT / 'querysource' / 'queries' / 'multi' / 'sources' / 'parquet' / 's3.py',
).ParquetS3Source

CREDS = {'bucket': 'bkt', 'region_name': 'us-east-1', 'aws_key': 'AKIATEST', 'aws_secret': 's3cr3t'}


def _make(options):
    return ParquetS3Source('pq_s3', options, None, asyncio.Queue())


def test_kwargs_explicit_creds():
    src = _make({'credentials': {**CREDS, 'endpoint_url': 'http://minio:9000'}, 'source': {'directory': 'd/'}})
    with patch('s3fs.S3FileSystem') as fs_cls:
        _, path = src._build_filesystem()
    kwargs = fs_cls.call_args.kwargs
    assert kwargs['key'] == 'AKIATEST' and kwargs['secret'] == 's3cr3t'
    assert kwargs['client_kwargs'] == {'region_name': 'us-east-1'}
    assert kwargs['endpoint_url'] == 'http://minio:9000'
    assert kwargs['skip_instance_cache'] is True
    assert path == 'bkt/d'


async def test_read_via_memory_fs():
    mem = MemoryFileSystem(skip_instance_cache=True)
    with mem.open('/bkt/d/x.parquet', 'wb') as fh:
        pq.write_table(pa.table({'a': [1, 2, 3]}), fh)
    src = _make({'credentials': CREDS, 'source': {'file': 'd/x.parquet'}})
    with patch('s3fs.S3FileSystem', return_value=mem):
        df = await src.fetch()
    assert df['a'].tolist() == [1, 2, 3]


def test_unresolved_names_fall_back_ambient():
    src = _make({'credentials': {'bucket': 'bkt', 'aws_key': 'SOME_UNSET_VAR', 'aws_secret': 'OTHER_UNSET_VAR'}})
    with patch('s3fs.S3FileSystem') as fs_cls:
        src._build_filesystem()
    kwargs = fs_cls.call_args.kwargs
    assert 'key' not in kwargs
    assert 'secret' not in kwargs


def test_profile_and_anon():
    src = _make({'credentials': {'bucket': 'bkt', 'profile': 'dev', 'anon': True}})
    with patch('s3fs.S3FileSystem') as fs_cls:
        src._build_filesystem()
    kwargs = fs_cls.call_args.kwargs
    assert kwargs['profile'] == 'dev'
    assert kwargs['anon'] is True


def test_storage_options_merged_last_but_cache_forced():
    src = _make(
        {
            'credentials': {'bucket': 'bkt', 'region_name': 'eu-west-1'},
            'storage_options': {'client_kwargs': {'signature_version': 's3v4'}, 'skip_instance_cache': False},
        }
    )
    with patch('s3fs.S3FileSystem') as fs_cls:
        src._build_filesystem()
    kwargs = fs_cls.call_args.kwargs
    assert kwargs['client_kwargs'] == {'signature_version': 's3v4'}
    assert kwargs['skip_instance_cache'] is True


def test_bucket_required():
    with pytest.raises(ValueError, match=r"credentials\.bucket"):
        _make({'credentials': {'bucket': 'AWS_S3_BUCKET'}})


def test_s3_path_with_masks():
    src = _make(
        {
            'credentials': {'bucket': 'bkt'},
            'source': {'directory': '/daily/{day}/', 'file': '/part.parquet/'},
            'masks': {'{day}': '2026-09-30'},
        }
    )
    fn_module = types.ModuleType('querysource.utils.fn')
    fn_module.fnExecutor = lambda spec: '2026-09-30'
    with patch.dict(sys.modules, {'querysource.utils.fn': fn_module}):
        assert src._build_s3_path() == 'bkt/daily/2026-09-30/part.parquet'


def test_errors_do_not_leak_secrets():
    src = _make({'credentials': CREDS})
    with patch('s3fs.S3FileSystem', side_effect=PermissionError('denied for s3cr3t')):
        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(src.fetch())
    assert 's3cr3t' not in str(exc_info.value)
