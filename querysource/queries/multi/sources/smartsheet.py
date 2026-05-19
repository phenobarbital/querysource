"""SourceSmartSheet — download a sheet from SmartSheet as a pandas DataFrame.

Fetches a SmartSheet spreadsheet via the SmartSheet REST API (GET as Excel)
and parses it into a pandas DataFrame.

Dependencies: ``aiohttp`` (already a project dependency).
"""
import asyncio
from io import BytesIO

import aiohttp
import pandas as pd
from aiohttp import web

from .base import ThreadSource


class SourceSmartSheet(ThreadSource):
    """Download a SmartSheet sheet as Excel and return it as a DataFrame.

    Authenticates via Bearer token (SmartSheet API key from navconfig or
    explicit configuration) and downloads the sheet with the given ``file_id``
    (sheet ID) in Excel format.

    Configuration dict shape::

        {
            "credentials": {
                "api_key": "SMARTSHEET_API_KEY"   # navconfig var or literal
            },
            "source": {
                "file_id": 8504624500658052
            }
        }

    If no ``credentials`` section is provided, ``SMARTSHEET_API_KEY`` is
    resolved from navconfig automatically.
    """

    BASE_URL = "https://api.smartsheet.com/2.0/sheets/"

    def __init__(
        self,
        name: str,
        options: dict,
        request: web.Request,
        queue: asyncio.Queue,
    ):
        super().__init__(name, options, request, queue)
        creds = options.get('credentials', {})
        self._api_key = self.resolve_credential(
            'api_key', creds.get('api_key', 'SMARTSHEET_API_KEY')
        )
        source = options.get('source', {})
        self._file_id = source.get('file_id')

    async def fetch(self) -> pd.DataFrame:
        """Fetch the SmartSheet sheet as Excel and parse into a DataFrame.

        Returns:
            A pandas DataFrame containing the sheet data.

        Raises:
            RuntimeError: If the API returns a rate-limit (429) or other
                non-2xx response.
            ValueError: If ``file_id`` is not provided.
        """
        if not self._file_id:
            raise ValueError("SourceSmartSheet: 'source.file_id' is required.")

        url = f"{self.BASE_URL}{self._file_id}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/vnd.ms-excel",
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 429:
                    raise RuntimeError("SmartSheet API rate limit exceeded (HTTP 429).")
                if resp.status == 401:
                    raise RuntimeError(
                        "SmartSheet API authentication failed (HTTP 401). "
                        "Check your API key."
                    )
                resp.raise_for_status()
                content = await resp.read()

        df = pd.read_excel(BytesIO(content), engine="openpyxl")
        df = df.infer_objects()
        return df
