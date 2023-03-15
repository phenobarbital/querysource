import time
from io import StringIO
from pathlib import PosixPath
from navconfig.logging import logging
import sweetviz as sv
from aiohttp import web
from .abstract import AbstractWriter

matlog = logging.getLogger('matplotlib')
matlog.setLevel(logging.INFO)

pil = logging.getLogger('PIL.PngImagePlugin')
pil.setLevel(logging.INFO)

class EDAWriter(AbstractWriter):
    mimetype: str = 'text/html'
    extension: str = '.html'
    ctype: str = 'html'
    output_format: str = 'pandas'
    explorative: bool = True

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        buffer = None
        result = StringIO()
        sv.config_parser.read("etc/sweetviz.ini")
        profile = sv.analyze((self.data, self.filename))
        ## saving sweetviz into a tmp file
        tmpfile = PosixPath('/tmp').joinpath(self.filename)
        # generate HTML
        profile.show_html(filepath=tmpfile, open_browser=False, layout='vertical', scale=None)
        result = None
        with open(tmpfile, 'r', encoding='utf-8') as fp:
            result = fp.read()
        buffer = bytes(result, 'utf-8')
        response = await self.response(self.response_type)
        content_length = len(buffer)
        response.content_length = content_length
        # next, deleting tmpfile
        try:
            tmpfile.unlink(missing_ok=True)
        except Exception as e:
            logging.exception(e)
        if self.download is True: # inmediately download response
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, buffer)
