"""Unit tests for ParquetS3Source (FEAT-158, TASK-799). No real AWS access."""
import asyncio
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fsspec.implementations.memory import MemoryFileSystem

from querysource.queries.multi.sources.parquet.s3 import ParquetS3Source

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
    assert src._build_s3_path() == 'bkt/daily/2026-09-30/part.parquet'


def test_errors_do_not_leak_secrets():
    src = _make({'credentials': CREDS})
    with patch('s3fs.S3FileSystem', side_effect=PermissionError('denied for s3cr3t')):
        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(src.fetch())
    assert 's3cr3t' not in str(exc_info.value)
