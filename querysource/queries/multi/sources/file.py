import asyncio
import threading
from aiohttp import web
from pathlib import Path
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

    def run(self):
        asyncio.set_event_loop(self._loop)
        try:
            # Open pandas File and load into Queue
            if self._mime in excel_based:
                ext = self.file_path.suffix
                if ext == ".xls":
                    file_engine = self._params.pop("file_engine", "xlrd")
                else:
                    file_engine = self._params.pop("file_engine", "openpyxl")
                df = pd.read_excel(
                    self.file_path,
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
        except Exception as ex:
            self.exc = ex
        finally:
            self._loop.close()
