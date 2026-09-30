"""ParquetGCSSource — Parquet from Google Cloud Storage via gcsfs (FEAT-158)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from querysource import conf

from .base import ParquetSource

if TYPE_CHECKING:
    import fsspec

_TOKEN_KEYWORDS = frozenset({"google_default", "anon", "cloud", "cache"})


class ParquetGCSSource(ParquetSource):
    """Read Parquet from Google Cloud Storage via gcsfs.

    Token precedence: explicit ``credentials.token`` → ``GOOGLE_CREDENTIALS_FILE``
    → ``BIGQUERY_CREDENTIALS`` → ``google_default`` (Application Default Credentials).
    """

    _extra = "gcs"

    def __init__(self, name: str, options: dict, request: web.Request,
                 queue: asyncio.Queue) -> None:
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._bucket = self.resolve_credential('bucket', creds.get('bucket'))
        project = creds.get('project', None)
        self._project = self.resolve_credential('project', project) if project else None
        self._token = creds.get('token', None)
        source = options.get('source', {})
        self._directory: str = source.get('directory', '')
        self._file: str = source.get('file', '')
        if not self._bucket or self._is_unresolved(self._bucket):
            raise ValueError("ParquetGCSSource: 'credentials.bucket' is required.")

    def _secrets(self) -> list[str]:
        """Redact string token values and service-account private keys from errors."""
        token = self._token
        if isinstance(token, dict):
            return [str(token[key]) for key in ("private_key", "private_key_id") if token.get(key)]
        return [token] if isinstance(token, str) and token else []

    def _resolve_token(self) -> str | dict:
        """Resolve the gcsfs token by the fixed precedence.

        Returns:
            A dict, a keyword from ``_TOKEN_KEYWORDS``, or a path to an existing SA JSON file.

        Raises:
            ValueError: an explicit token resolves to a path that does not exist.
        """
        token = self._token
        if isinstance(token, dict):
            return token
        if isinstance(token, str) and token:
            if token in _TOKEN_KEYWORDS:
                return token
            resolved = self.resolve_credential('token', token)
            if self._is_unresolved(resolved):
                self.logger.warning(
                    "%s: credentials.token %r could not be resolved; using fallbacks.",
                    self._name, token,
                )
            else:
                if isinstance(resolved, dict):
                    return resolved
                if resolved in _TOKEN_KEYWORDS:
                    return resolved
                if not Path(str(resolved)).is_file():
                    shown = str(resolved)
                    if shown.lstrip().startswith(("{", "-----")):
                        shown = "<inline credential>"
                    raise ValueError(f"ParquetGCSSource: credentials file not found: {shown}")
                return str(resolved)
        for candidate in (conf.GOOGLE_CREDENTIALS_FILE, conf.BIGQUERY_CREDENTIALS):
            if candidate and Path(candidate).is_file():
                return str(candidate)
        return "google_default"

    def _build_gcs_path(self) -> str:
        """Return '<bucket>/<directory>/<file>' with masks resolved and slashes normalized."""
        parts = [
            str(self.resolve_masks(part)).strip("/")
            for part in (self._bucket, self._directory, self._file)
            if part
        ]
        return "/".join(part for part in parts if part)

    def _build_filesystem(self) -> tuple[fsspec.AbstractFileSystem, str]:
        """Build ``gcsfs.GCSFileSystem(project=..., token=..., skip_instance_cache=True, **storage_options)``."""
        try:
            import gcsfs  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError("Install the gcs extra for ParquetGCSSource: pip install querysource[gcs]") from exc
        kwargs: dict = {"token": self._resolve_token()}
        if self._project:
            kwargs["project"] = self._project
        kwargs.update(self._storage_options)
        kwargs["skip_instance_cache"] = True
        return gcsfs.GCSFileSystem(**kwargs), self._build_gcs_path()
