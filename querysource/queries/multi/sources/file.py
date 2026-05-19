import asyncio
import gzip
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd
from aiohttp import web

from .base import ThreadSource

excel_based = (
    "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/xml",
)


class ThreadFile(ThreadSource):
    """ThreadFile loads a static file from a local path.

    Reads the file (CSV or Excel, optionally compressed with gzip or zip)
    and puts the resulting DataFrame into the shared queue.  Inherits the
    thread/event-loop/queue boilerplate from :class:`ThreadSource`.
    """

    def __init__(
        self,
        name: str,
        file_options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ):
        # Extract file-specific options BEFORE passing remaining dict to super.
        self.file_path = file_options.pop('path')
        if isinstance(self.file_path, str):
            self.file_path = Path(self.file_path).resolve()
        self._mime = file_options.pop('mime')
        self._params: dict = file_options
        super().__init__(name, file_options, request, queue)

    def _get_file_content(self):
        """Return file content, handling compressed files if needed."""
        file_suffix = self.file_path.suffix.lower()

        if file_suffix == '.zip':
            with zipfile.ZipFile(self.file_path, 'r') as zip_ref:
                file_name = zip_ref.namelist()[0]
                return BytesIO(zip_ref.read(file_name))

        elif file_suffix == '.gz':
            with gzip.open(self.file_path, 'rb') as gz_file:
                return BytesIO(gz_file.read())

        else:
            return self.file_path

    async def fetch(self) -> pd.DataFrame:
        """Read the file and return it as a DataFrame."""
        file_content = self._get_file_content()

        if self._mime in excel_based:
            ext = self.file_path.suffix
            if ext in ('.zip', '.gz'):
                inner_ext = Path(self.file_path.stem).suffix
                if inner_ext == ".xls":
                    file_engine = self._params.pop("file_engine", "xlrd")
                else:
                    file_engine = self._params.pop("file_engine", "openpyxl")
            elif ext == ".xls":
                file_engine = self._params.pop("file_engine", "xlrd")
            else:
                file_engine = self._params.pop("file_engine", "openpyxl")
            df = pd.read_excel(
                file_content,
                na_values=["NULL", "TBD"],
                na_filter=True,
                engine=file_engine,
                keep_default_na=False,
                **self._params,
            )
        elif self._mime == 'text/csv':
            df = pd.read_csv(
                file_content,
                na_values=["NULL", "TBD"],
                na_filter=True,
                keep_default_na=False,
                **self._params,
            )
        else:
            raise ValueError(f"Unsupported MIME type: {self._mime}")

        df.infer_objects()
        return df
