import time
from io import BytesIO
from aiohttp import web
from .abstract import AbstractWriter

class TableWriter(AbstractWriter):
    mimetype: str = 'application/json'
    extension: str = '.json'
    ctype: str = 'json'
    output_format: str = 'pandas'
    orient: str = 'table'

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        buffer = None
        with BytesIO() as output:
            self.data.to_json(
                output,
                orient=self.orient
            )
            output.seek(0)
            buffer = output.getvalue()
        response = await self.response(self.response_type)
        # if self.download is True: # inmediately download response
        content_length = len(buffer)
        response.content_length = content_length
        response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
        if self.download is True: # inmediately download response
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, buffer)
