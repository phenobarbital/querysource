import time
import logging
from pandas_profiling import ProfileReport
from aiohttp import web
from ...conf import URL_PROFILING
from .abstract import AbstractWriter

matlog = logging.getLogger('matplotlib.font_manager')
matlog.setLevel(logging.WARNING)

class ProfileWriter(AbstractWriter):
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
        profile = ProfileReport(
            self.data,
            title=self.filename,
            explorative=self.explorative,
            dataset={
                "description": "This profiling report was generated using QuerySource",
                "copyright_holder": "QuerySource",
                "copyright_year": 2023,
                "url": URL_PROFILING,
            },
            correlations=None,
            pool_size=0
        )
        # enable great expectations:
        # suite = profile.to_expectation_suite(suite_name="querysource_expectation")
        # generate HTML
        buffer = bytes(profile.to_html(), 'utf-8')
        response = await self.response(self.response_type)
        content_length = len(buffer)
        response.content_length = content_length
        if self.download is True: # inmediately download response
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, buffer)
