"""SourceS3 — download a single file from an AWS S3 bucket as a pandas DataFrame.

Downloads a single file from S3 using the async ``aioboto3`` library.
Supports CSV, compressed CSV (.gz), and Excel (.xlsx / .xls) files.

Optional dependency: ``aioboto3``.
Install with: ``pip install querysource[s3]``
"""
import asyncio
import gzip
from io import BytesIO
from pathlib import Path

import pandas as pd
from aiohttp import web

from .base import ThreadSource
from .file import excel_based

# Warn if a downloaded file exceeds this size (100 MB).
_SIZE_WARNING_BYTES = 100 * 1024 * 1024


class SourceS3(ThreadSource):
    """Download a single file from an S3 bucket and return it as a DataFrame.

    Authenticates with AWS using explicit credentials (resolved via navconfig
    if the values look like environment variable names) or falls back to the
    default AWS credential chain.

    Configuration dict shape::

        {
            "credentials": {
                "region_name": "AWS_REGION_NAME",        # navconfig var or literal
                "bucket": "AWS_PLACER_BUCKET",
                "aws_key": "AWS_ACCESS_KEY_ID",
                "aws_secret": "AWS_SECRET_ACCESS_KEY"
            },
            "source": {
                "file": "metrics_2024-12-09_0003.csv.gz",
                "directory": "placer-analytics/bulk-export/"
            }
        }
    """

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._region = self.resolve_credential(
            'region_name', creds.get('region_name', 'AWS_REGION_NAME')
        )
        self._bucket = self.resolve_credential(
            'bucket', creds.get('bucket', 'AWS_S3_BUCKET')
        )
        self._aws_key = self.resolve_credential(
            'aws_key', creds.get('aws_key', 'AWS_ACCESS_KEY_ID')
        )
        self._aws_secret = self.resolve_credential(
            'aws_secret', creds.get('aws_secret', 'AWS_SECRET_ACCESS_KEY')
        )
        source = options.get('source', {})
        self._file: str = source.get('file', '')
        self._directory: str = source.get('directory', '')

    def _build_s3_key(self) -> str:
        """Build the S3 object key from directory and file name."""
        if self._directory:
            return f"{self._directory.rstrip('/')}/{self._file}"
        return self._file

    def _parse_content(self, content: bytes, filename: str) -> pd.DataFrame:
        """Decompress (if needed) and parse bytes as a DataFrame."""
        if len(content) > _SIZE_WARNING_BYTES:
            self.logger.warning(
                "SourceS3: downloaded %d bytes (%.1f MB) — consider streaming "
                "for files larger than 100 MB.",
                len(content),
                len(content) / (1024 * 1024),
            )

        # Decompress .gz files
        if filename.endswith('.gz'):
            content = gzip.decompress(content)
            inner_name = filename[:-3]  # strip .gz to get the real extension
        else:
            inner_name = filename

        buf = BytesIO(content)
        suffix = Path(inner_name).suffix.lower()

        # Determine whether Excel or CSV
        ext_to_mime = {
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.xls': 'application/vnd.ms-excel',
            '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
            '.xlsb': 'application/vnd.ms-excel.sheet.binary.macroEnabled.12',
        }
        mime = ext_to_mime.get(suffix, 'text/csv')

        if mime in excel_based:
            engine = 'xlrd' if suffix == '.xls' else 'openpyxl'
            df = pd.read_excel(
                buf,
                engine=engine,
                na_values=["NULL", "TBD"],
                na_filter=True,
                keep_default_na=False,
            )
        else:
            df = pd.read_csv(
                buf,
                na_values=["NULL", "TBD"],
                na_filter=True,
                keep_default_na=False,
            )

        df = df.infer_objects()
        return df

    async def fetch(self) -> pd.DataFrame:
        """Download the S3 object and return it as a DataFrame.

        Raises:
            ImportError: If ``aioboto3`` is not installed.
            ValueError: If ``source.file`` is not provided.
            RuntimeError: On S3 download errors.
        """
        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "Install aioboto3 for S3 source support: "
                "pip install querysource[s3]"
            ) from exc

        if not self._file:
            raise ValueError("SourceS3: 'source.file' is required.")

        s3_key = self._build_s3_key()

        session = aioboto3.Session()
        client_kwargs: dict = {
            'region_name': self._region,
        }
        # Only pass explicit credentials if they are not clearly unresolved
        # env var names (i.e., if they were successfully resolved to real values).
        if self._aws_key and not (self._aws_key.isupper() and '_' in self._aws_key):
            client_kwargs['aws_access_key_id'] = self._aws_key
        if self._aws_secret and not (
            self._aws_secret.isupper() and '_' in self._aws_secret
        ):
            client_kwargs['aws_secret_access_key'] = self._aws_secret

        async with session.client('s3', **client_kwargs) as client:
            response = await client.get_object(Bucket=self._bucket, Key=s3_key)
            content = await response['Body'].read()

        return self._parse_content(content, self._file)
