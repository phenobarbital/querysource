"""
ToS3 Destination.

Converts a pandas DataFrame to a file (CSV, CSV.GZ, Parquet, or Excel) and
uploads it to an AWS S3 bucket using ``aioboto3``.

YAML configuration example::

    Output:
      - ToS3:
          credentials:
            region_name: "AWS_REGION_NAME"
            bucket: "AWS_PLACER_BUCKET"
            aws_key: "AWS_ACCESS_KEY_ID"
            aws_secret: "AWS_SECRET_ACCESS_KEY"
          destination:
            file: "metrics_2024-12-09.csv.gz"
            directory: "placer-analytics/bulk-export/2024-12-09/"

All credential values may be literal strings or navconfig variable names
(ALL_CAPS_SNAKE_CASE) resolved at runtime.

Output format is inferred from the filename extension unless overridden by an
explicit ``format`` key in the destination config:

* ``.csv``     → CSV (UTF-8)
* ``.csv.gz``  → CSV + gzip
* ``.parquet`` → Parquet (via *pyarrow*)
* ``.xlsx``    → Excel (via *openpyxl*)
* anything else → CSV (default)
"""
import gzip
import io
from pathlib import PurePosixPath
from typing import Union
import pandas as pd
from querysource.exceptions import OutputError
from .abstract import AbstractDestination


# Content-type mapping for common extensions
_CONTENT_TYPES: dict[str, str] = {
    ".csv": "text/csv",
    ".csv.gz": "application/gzip",
    ".parquet": "application/octet-stream",
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument"
        ".spreadsheetml.sheet"
    ),
}


def _detect_format(filename: str, explicit_format: str | None = None) -> str:
    """
    Return the output format string for *filename*.

    Priority: explicit ``format`` config > filename extension.

    :param filename: Target filename (used for extension detection).
    :param explicit_format: Optional explicit format override.
    :returns: One of ``"csv"``, ``"csv.gz"``, ``"parquet"``, ``"xlsx"``.
    """
    if explicit_format:
        return explicit_format.lower().lstrip(".")

    name = filename.lower()
    if name.endswith(".csv.gz"):
        return "csv.gz"
    ext = PurePosixPath(name).suffix.lstrip(".")
    return ext if ext in ("csv", "parquet", "xlsx") else "csv"


class ToS3(AbstractDestination):
    """
    Upload a DataFrame to an AWS S3 bucket.

    The destination ``directory`` and ``file`` config keys are joined to form
    the S3 object key::

        s3_key = directory.strip("/") + "/" + file

    For example::

        directory: "exports/2025/"
        file: "output.csv"
        → s3_key: "exports/2025/output.csv"
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        super().__init__(data, **kwargs)

        raw_creds: dict = kwargs.get("credentials", {}) or {}
        dest_cfg: dict = kwargs.get("destination", {}) or {}

        resolved = self.resolve_credentials(raw_creds)

        self._region: str = resolved.get("region_name", "us-east-1")
        self._bucket: str = resolved.get("bucket", "")
        self._aws_key: str = resolved.get("aws_key", "")
        self._aws_secret: str = resolved.get("aws_secret", "")

        self._file: str = dest_cfg.get("file", "output.csv")
        self._directory: str = dest_cfg.get("directory", "").strip("/")
        self._explicit_format: str | None = dest_cfg.get("format")

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    def _build_s3_key(self) -> str:
        """
        Build the full S3 object key from directory and filename.

        :returns: S3 key string (no leading slash).
        """
        if self._directory:
            return f"{self._directory}/{self._file}"
        return self._file

    # ------------------------------------------------------------------
    # DataFrame serialisation
    # ------------------------------------------------------------------

    def _convert_dataframe(self, df: pd.DataFrame, filename: str) -> bytes:
        """
        Serialize *df* to bytes.

        The format is determined by :func:`_detect_format`.

        :param df: Source DataFrame.
        :param filename: Target filename (used for extension detection when no
            explicit format is configured).
        :returns: Serialised bytes.
        :raises OutputError: On serialisation failure.
        """
        fmt = _detect_format(filename, self._explicit_format)
        try:
            if fmt == "csv.gz":
                csv_bytes = df.to_csv(index=False).encode("utf-8")
                return gzip.compress(csv_bytes)
            elif fmt == "parquet":
                buf = io.BytesIO()
                df.to_parquet(buf, index=False, engine="pyarrow")
                return buf.getvalue()
            elif fmt == "xlsx":
                buf = io.BytesIO()
                df.to_excel(buf, index=False, engine="openpyxl")
                return buf.getvalue()
            else:
                # Default: CSV
                return df.to_csv(index=False).encode("utf-8")
        except Exception as err:
            raise OutputError(
                f"ToS3: failed to serialise DataFrame to {fmt!r}: {err}"
            ) from err

    def _content_type(self, filename: str) -> str:
        """Return the MIME content-type for *filename*."""
        name = filename.lower()
        if name.endswith(".csv.gz"):
            return _CONTENT_TYPES[".csv.gz"]
        ext = "." + PurePosixPath(name).suffix.lstrip(".")
        return _CONTENT_TYPES.get(ext, "application/octet-stream")

    # ------------------------------------------------------------------
    # S3 upload
    # ------------------------------------------------------------------

    async def _upload_to_s3(self, content: bytes, s3_key: str) -> None:
        """
        Upload *content* to :attr:`_bucket` under *s3_key*.

        :param content: File bytes to upload.
        :param s3_key: S3 object key (path within the bucket).
        :raises OutputError: On authentication or upload failure.
        """
        try:
            import aioboto3
        except ImportError as exc:
            raise OutputError(
                "ToS3 requires 'aioboto3'. Install it with: pip install aioboto3"
            ) from exc

        if not self._bucket:
            raise OutputError(
                "ToS3: 'bucket' is required in the credentials config."
            )

        session = aioboto3.Session(
            aws_access_key_id=self._aws_key or None,
            aws_secret_access_key=self._aws_secret or None,
            region_name=self._region,
        )

        content_type = self._content_type(self._file)

        try:
            async with session.client("s3") as s3:
                self.logger.info(
                    "ToS3: uploading %d bytes → s3://%s/%s",
                    len(content),
                    self._bucket,
                    s3_key,
                )
                await s3.put_object(
                    Bucket=self._bucket,
                    Key=s3_key,
                    Body=content,
                    ContentType=content_type,
                )
                self.logger.info(
                    "ToS3: upload complete → s3://%s/%s", self._bucket, s3_key
                )
        except Exception as err:
            raise OutputError(
                f"ToS3: upload to s3://{self._bucket}/{s3_key} failed: {err}"
            ) from err

    # ------------------------------------------------------------------
    # AbstractDestination interface
    # ------------------------------------------------------------------

    async def run(self) -> Union[dict, pd.DataFrame]:
        """
        Convert :attr:`data` and upload to S3.

        Handles both a single :class:`~pandas.DataFrame` and a ``dict``
        of DataFrames (each is uploaded as a separate file with the dict key
        embedded in the filename).

        :returns: Original :attr:`data` (pass-through).
        :raises OutputError: On serialisation or upload failure.
        """
        try:
            if isinstance(self.data, dict):
                for key, df in self.data.items():
                    if not isinstance(df, pd.DataFrame):
                        continue
                    stem = PurePosixPath(self._file).stem
                    suffix = "".join(
                        PurePosixPath(self._file).suffixes
                    )
                    target_file = f"{stem}_{key}{suffix}"
                    base_dir = self._directory
                    s3_key = f"{base_dir}/{target_file}" if base_dir else target_file
                    content = self._convert_dataframe(df, target_file)
                    await self._upload_to_s3(content, s3_key)
            else:
                s3_key = self._build_s3_key()
                content = self._convert_dataframe(self.data, self._file)
                await self._upload_to_s3(content, s3_key)
        except OutputError:
            raise
        except Exception as err:
            raise OutputError(
                f"ToS3: unexpected error: {err}"
            ) from err

        return self.data
