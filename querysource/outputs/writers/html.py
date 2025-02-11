import time
from io import StringIO
from pandas import DataFrame
from aiohttp import web
from .abstract import AbstractWriter

class HTMLWriter(AbstractWriter):
    mimetype: str = 'text/html'
    extension: str = '.html'
    ctype: str = 'html'
    download: bool = False
    output_format: str = 'pandas'

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        elif self.content_type == 'text/html':
            self.extension = '.html'
        else:
            self.extension = '.htm'
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        if isinstance(self.data, DataFrame):
            output = StringIO()
            # create the HTML file:
            columns = list(self.data.columns)
            dimensions = self.kwargs.get('show_dimensions', False)
            self.data.to_html(
                output,
                columns=columns,
                header=True,
                index=False,
                classes='table table-stripped',
                bold_rows=True,
                # escape=True,
                border=1,
                show_dimensions=dimensions,
                table_id="qs_table"
            )
            output.seek(0)
            buffer = output.getvalue()
        elif isinstance(self.data, str):
            # returning as-is
            buffer = self.data
        response = await self.response(self.response_type)
        # if self.download is True: # inmediately download response
        content_length = len(buffer)
        response.content_length = content_length
        # response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
        if self.download is True:  # inmediately download response
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, bytes(buffer, 'utf-8'))
