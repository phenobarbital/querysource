from aiohttp import web
from .abstract import AbstractWriter

class TXTWriter(AbstractWriter):
    mimetype: str = 'text/plain'
    extension: str = '.txt'
    ctype: str = 'txt'
    output_format: str = 'iter'

    async def get_response(self) -> web.StreamResponse:
        try:
            data = bytes(self._json.dumps(self.data), 'utf-8')
        except ValueError as ex:
            return self.error(
                message="Error parsing JSON Data",
                exception=ex,
                status=500
            )
        response = await self.response(self.response_type)
        content_length = len(data)
        response.content_length = content_length
        response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
        if self.download is True: # inmediately download response
            await response.prepare(self.request)
            await response.write(data)
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, data)
