"""ParquetS3Source — Parquet from AWS S3 or S3-compatible storage via s3fs (FEAT-158)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web

from .base import ParquetSource

if TYPE_CHECKING:
    import fsspec


class ParquetS3Source(ParquetSource):
    """Read Parquet from AWS S3 or an S3-compatible endpoint via s3fs.

    Credential values may be navconfig variable names. Names that stay unresolved
    are ignored, so s3fs falls back to the ambient AWS credential chain (as S3Source does).
    """

    _extra = "s3"

    def __init__(self, name: str, options: dict, request: web.Request, queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._bucket = self.resolve_credential('bucket', creds.get('bucket', 'AWS_S3_BUCKET'))
        self._region = self.resolve_credential('region_name', creds.get('region_name', 'AWS_REGION_NAME'))
        self._aws_key = self.resolve_credential('aws_key', creds.get('aws_key', 'AWS_ACCESS_KEY_ID'))
        self._aws_secret = self.resolve_credential(
            'aws_secret', creds.get('aws_secret', 'AWS_SECRET_ACCESS_KEY')
        )
        self._profile = creds.get('profile', None)
        self._anon = bool(creds.get('anon', False))
        self._endpoint_url = self.resolve_credential('endpoint_url', creds.get('endpoint_url', None))
        source = options.get('source', {})
        self._directory: str = source.get('directory', '')
        self._file: str = source.get('file', '')
        if not self._bucket or self._is_unresolved(self._bucket):
            raise ValueError(
                "ParquetS3Source: 'credentials.bucket' is required (literal or resolvable navconfig name)."
            )

    def _secrets(self) -> list[str]:
        """Redact resolved AWS key and secret from error messages."""
        return [value for value in (self._aws_key, self._aws_secret) if value and not self._is_unresolved(value)]

    def _build_s3_path(self) -> str:
        """Return '<bucket>/<directory>/<file>' with masks resolved and slashes normalized."""
        parts = (self._bucket, self.resolve_masks(self._directory), self.resolve_masks(self._file))
        return '/'.join(str(part).strip('/') for part in parts if part)

    def _build_filesystem(self) -> tuple["fsspec.AbstractFileSystem", str]:
        """Build ``s3fs.S3FileSystem`` and return it with the source path."""
        try:
            import s3fs  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError("Install the s3 extra for ParquetS3Source: pip install querysource[s3]") from exc

        kwargs: dict = {'skip_instance_cache': True}
        if self._aws_key and not self._is_unresolved(self._aws_key):
            kwargs['key'] = self._aws_key
        if self._aws_secret and not self._is_unresolved(self._aws_secret):
            kwargs['secret'] = self._aws_secret
        if self._profile:
            kwargs['profile'] = self._profile
        if self._anon:
            kwargs['anon'] = True
        if self._endpoint_url and not self._is_unresolved(self._endpoint_url):
            kwargs['endpoint_url'] = self._endpoint_url
        if self._region and not self._is_unresolved(self._region):
            kwargs['client_kwargs'] = {'region_name': self._region}
        kwargs.update(self._storage_options)
        kwargs['skip_instance_cache'] = True
        return s3fs.S3FileSystem(**kwargs), self._build_s3_path()
