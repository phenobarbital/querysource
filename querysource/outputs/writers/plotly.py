from typing import Any, Union
import time
from io import StringIO
import pandas
import plotly.express as px
from aiohttp import web
from .abstract import AbstractWriter

class PlotlyWriter(AbstractWriter):
    mimetype: str = 'text/html'
    extension: str = '.html'
    ctype: str = 'html'
    download: bool = False

    def __init__(
        self,
        request: web.Request,
        resultset: Any,
        filename: str = None,
        response_type: str = 'web',
        download: bool = False,
        compression: Union[list, str] = None,
        ctype: str = None,
        **kwargs
    ):
        super(PlotlyWriter, self).__init__(
            request,
            resultset,
            filename=filename,
            response_type=response_type,
            download=download,
            compression=compression,
            ctype=ctype,
            **kwargs
        )
        if 'title' in kwargs:
            self._title = kwargs['title']
        else:
            self._title = filename.replace('_', ' ').capitalize()
        ### getting X and Y arguments:
        self._x = None
        self._y = None
        if 'xaxis' in kwargs:
            self._x = kwargs['xaxis']
        if 'xtitle' in kwargs:
            self._xtitle = kwargs['xtitle']
        if 'yaxis' in kwargs:
            self._y = kwargs['yaxis']
        # plot type:
        if 'type' in kwargs:
            self._type = kwargs['type']
        else:
            self._type = 'area'

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
            pandas.options.plotting.backend = "plotly"
            data = await self.get_buffer()
            df = pandas.DataFrame(data, columns=self.columns)
        except ValueError as ex:
            return self.error(
                message="Error parsing JSON Data",
                exception=ex,
                status=500
            )
        # creating a figure:
        output = StringIO()
        # fig = df.plot.bar()
        columns = self.columns.copy()
        if not self._x: # there is no X series:
            self._x = columns.pop(0)
        if not self._y:
            self._y = columns

        if self._type == 'bar':
            graph = px.bar
        elif self._type == 'area':
            graph = px.area
        else:
            # default plot
            graph = px.line
        # TODO: using "args" and Partial
        fig = graph(df, x=self._x, y=self._y, title=self._title)
        # df = px.data.iris() # replace with your own data source
        # fig = px.scatter(
        #     df, x="sepal_width", y="sepal_length",
        #     color="species"
        # )
        fig.write_html(output)
        buffer = output.getvalue()
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
