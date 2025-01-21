import csv
from io import StringIO
from aiocsv import AsyncDictWriter
from aiohttp import web
from ...conf import (
    CSV_DEFAULT_DELIMITER,
    CSV_DEFAULT_QUOTING
)
from .abstract import AbstractWriter


class TmpFile:
    def open_buffer(self):
        self.output = StringIO()
        return self

    async def write(self, data):
        self.output.write(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.output.seek(0)
        return self

    def get(self):
        return self.output.getvalue()

class CSVWriter(AbstractWriter):
    mimetype: str = 'text/csv'
    extension: str = '.csv'
    ctype: str = 'csv'
    output_format: str = 'iter'

    async def get_response(self) -> web.StreamResponse:
        try:
            await self.get_buffer()
            if 'delimiter' in self.kwargs:
                delimiter = self.kwargs['delimiter']
            else:
                delimiter = CSV_DEFAULT_DELIMITER
            if 'quoting' in self.kwargs:
                quoting = self.kwargs['quoting']
            else:
                quoting = CSV_DEFAULT_QUOTING
            if quoting == 'all':
                qt = csv.QUOTE_ALL
            elif quoting == 'string':
                qt = csv.QUOTE_NONNUMERIC
            elif quoting == 'minimal':
                qt = csv.QUOTE_MINIMAL
            else:
                qt = csv.QUOTE_NONE
            tmp = TmpFile()
            async with tmp.open_buffer() as afp:
                writer = AsyncDictWriter(
                    afp,
                    self.columns,
                    restval="NULL",
                    quoting=qt,
                    delimiter=delimiter,
                    skipinitialspace=True
                )
                await writer.writeheader()
                await writer.writerows(self.data)
            buffer = tmp.get()
        except ValueError as ex:
            return self.error(
                message="Error parsing Data",
                exception=ex,
                status=500
            )
        response = await self.response(self.response_type)
        content_length = len(buffer)
        response.content_length = content_length
        response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
        if self.download is True:  # inmediately download response
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, bytes(buffer, 'utf-8'))
