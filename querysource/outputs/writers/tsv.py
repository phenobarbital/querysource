import csv
from io import StringIO
from aiocsv import AsyncDictWriter
from aiohttp import web
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

class TSVWriter(AbstractWriter):
    mimetype: str = 'text/csv'
    extension: str = '.tsv'
    ctype: str = 'tsv'
    output_format: str = 'iter'

    async def get_response(self) -> web.StreamResponse:
        try:
            await self.get_buffer()
            tmp = TmpFile()
            async with tmp.open_buffer() as afp:
                writer = AsyncDictWriter(afp, self.columns, restval="NULL", quoting=csv.QUOTE_NONE, delimiter='\t', skipinitialspace=True)
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
        if self.download is True: # inmediately download response
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, bytes(buffer, 'utf-8'))
