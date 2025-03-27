import asyncio
import threading
from aiohttp import web
from pathlib import Path
import zipfile
import gzip
from io import BytesIO
import pandas as pd

excel_based = (
    "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/xml",
)

# await self._queue.put({self._name: result})
class ThreadFile(threading.Thread):
    """ThreadQuery is a class that will run a QueryObject in a separate thread."""
    def __init__(self, name: str, file_options: dict, request: web.Request, queue: asyncio.Queue):
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._queue = queue
        self.exc = None
        self._name = name
        self.file_path = file_options.pop('path')
        if isinstance(self.file_path, str):
            self.file_path = Path(self.file_path).resolve()
        self._mime = file_options.pop('mime')
        self._params: dict = file_options

    def _get_file_content(self):
        """Get file content, handling compressed files if needed."""
        file_suffix = self.file_path.suffix.lower()

        if file_suffix == '.zip':
            # Handle zip files
            with zipfile.ZipFile(self.file_path, 'r') as zip_ref:
                # Get the first file in the archive
                # You might want to add logic to select a specific file
                file_name = zip_ref.namelist()[0]
                return BytesIO(zip_ref.read(file_name))

        elif file_suffix == '.gz':
            # Handle gzip files
            with gzip.open(self.file_path, 'rb') as gz_file:
                return BytesIO(gz_file.read())

        else:
            # Return the file path directly for non-compressed files
            return self.file_path

    def run(self):
        """Execute the operation of open a File and load into Queue."""
        asyncio.set_event_loop(self._loop)
        try:
            # Get file content (handles compressed files)
            file_content = self._get_file_content()
            # Open pandas File and load into Queue
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
                    **self._params
                )
                df.infer_objects()
                self._loop.run_until_complete(
                    self._queue.put({self._name: df})
                )
            elif self._mime == 'text/csv':
                df = pd.read_csv(
                    file_content,
                    na_values=["NULL", "TBD"],
                    na_filter=True,
                    keep_default_na=False,
                    **self._params
                )
                df.infer_objects()
                self._loop.run_until_complete(
                    self._queue.put({self._name: df})
                )
        except Exception as ex:
            self.exc = ex
        finally:
            self._loop.close()
