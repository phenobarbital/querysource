import time
import pandas
from bokeh.plotting import figure
from bokeh.resources import CDN
from bokeh.embed import file_html
from bokeh.models import ColumnDataSource
from aiohttp import web
from .abstract import AbstractWriter

class BokehWriter(AbstractWriter):
    mimetype: str = 'text/html'
    extension: str = '.html'
    ctype: str = 'html'
    download: bool = False

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        elif self.content_type == 'text/html':
            self.extension = '.html'
        else:
            self.extension = '.htm'
        return f"{dt}-{filename}{self.extension}"

    async def get_buffer(self):
        if isinstance(self.data, list):
            rec = self.data[0]
            resultset = [dict(row) for row in self.data]
        else:
            rec = self.data
            resultset = self.data
        try:
            self.columns = list(rec.keys())
        except (KeyError, AttributeError, TypeError) as e:
            self.logger.error(e)
        return resultset

    async def get_response(self) -> web.StreamResponse:
        try:
            pandas.set_option('plotting.backend', 'pandas_bokeh')
            data = await self.get_buffer()
            df = pandas.DataFrame(data, columns=self.columns)
        except ValueError as ex:
            return self.error(
                message="Error parsing JSON Data",
                exception=ex,
                status=500
            )
        source = ColumnDataSource(df)
        plot = figure()
        plot.line(
            x='order_date',
            y='total',
            source=source
        )
        plot.xaxis.axis_label = 'Order Date'
        plot.yaxis.axis_label = 'Total Sales'
        # create the HTML file:
        # Users can use get_screenshot_as_png to get an image object instead of writing a file
        buffer = file_html(plot, CDN, "Sample Plot")
        response = await self.response(self.response_type)
        # if self.download is True: # inmediately download response
        content_length = len(buffer)
        response.content_length = content_length
        # response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
        if self.download is True: # inmediately download response
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, bytes(buffer, 'utf-8'))
