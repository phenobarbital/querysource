import time
from io import BytesIO
import base64
from typing import Any, Union
from aiohttp import web
import pygal
import pandas
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from .abstract import AbstractWriter


def render_seaborn(data, chart_type, info: dict):
    image_data = None
    if chart_type == 'heatmap':
        columns = info['columns']
        data = data[columns]
        plt.figure(figsize=(10, 16))
        if 'correlation_matrix' in info:
            col = info['correlation_matrix']
            correlation_matrix = pandas.DataFrame(
                0,
                index=data[col],
                columns=data[col]
            )
            np.fill_diagonal(
                correlation_matrix.values, data['Correlation']
            )
            ax = sns.heatmap(
                correlation_matrix,
                vmin=-1, vmax=1,
                cmap=sns.diverging_palette(20, 220, n=200, as_cmap=True),
                square=True,
                annot=True,
                center=0,
                linewidths=.5,  # Width of lines that divide cells
            )
            plt.xticks(rotation=45, ha='right')
            plt.yticks(rotation=0)
        else:
            ax = sns.heatmap(
                data,
                vmin=-1, vmax=1,
                cmap=sns.diverging_palette(20, 220, n=200, as_cmap=True),
                square=True,
                annot=True,
                center=0,
                linewidths=.5,  # Width of lines that divide cells
            )
            ax.set_xticklabels(
                # ax.get_xticklabels(),
                columns,
                rotation=45,
                horizontalalignment='right'
            )
            ax.set_yticklabels(
                columns,
                rotation=0
            )
        if 'title' in info:
            plt.title(info['title'])
        # Adjusts plot to ensure everything fits without overlap
        plt.tight_layout()
        sns_figure = ax.get_figure()
        buffer = BytesIO()
        sns_figure.savefig(buffer, format='png')
        buffer.seek(0)
    elif chart_type == 'pairplot':
        sns.set(style="ticks")
        sns.pairplot(data)
        buffer = BytesIO()
        plt.savefig(buffer, format='png')
        buffer.seek(0)
    elif chart_type == 'colored-bar':
        columns = info['columns']
        data = data[columns]
        col1 = columns[1]
        col0 = columns[0]
        norm = plt.Normalize(data[col1].min(), data[col1].max())
        colors = plt.cm.coolwarm(norm(data[col1]))
        # Set up the figure and axes for the bar chart
        fig, ax = plt.subplots(figsize=(10, 8))
        bars = ax.bar(data[col0], data[col1], color=colors)
        # Create the colorbar and link it explicitly to the axes
        plt.colorbar(
            plt.cm.ScalarMappable(norm=norm, cmap='coolwarm'),
            ax=ax,
            label='Correlation Coefficient'
        )
        ax.set_xticks(range(len(data[col0])))
        ax.set_xticklabels(data[col0], rotation=45, ha='right')
        ax.set_ylabel('Correlation Coefficient')
        ax.set_title('Visual Correlation by Program')
        plt.tight_layout()
        buffer = BytesIO()
        plt.savefig(buffer, format='png')
        buffer.seek(0)
    elif chart_type == 'line-chart':

        x_axis = info['x_axis']
        y_axis = info['y_axis']
        legend = info['legend']
        data[x_axis] = pandas.to_datetime(
            data[x_axis],
            unit='ms'
        )
        # Plotting
        fig, ax = plt.subplots(figsize=(18, 12))
        # Group by 'Program' and plot each group
        for label, group_df in data.groupby(legend):
            ax.plot(group_df[x_axis], group_df[y_axis], label=label)

        plt.gca().xaxis.set_major_locator(plt.MaxNLocator(15))  # Reduce number of x-ticks
        plt.gcf().autofmt_xdate()  # Rotate date labels to prevent overlap

        # Set Y-limits dynamically based on data
        y_min = data[y_axis].min()
        y_max = data[y_axis].max()
        padding = 0.1 * (y_max - y_min)
        plt.ylim(y_min - padding, y_max + padding)

        # Positioning legend on the left outside of the plot
        plt.legend(title=legend, loc='center left', bbox_to_anchor=(-0.05, 0.5))
        ax.set_xlabel(x_axis)
        ax.set_ylabel(y_axis)
        ax.set_title(info['title'])
        # Rotate date labels for better visibility
        plt.grid(True)
        plt.tight_layout()
        buffer = BytesIO()
        plt.savefig(buffer, format='png', bbox_inches="tight")
        buffer.seek(0)

    # Return image as an bytes object
    image_data = "data:image/png;charset=utf-8;base64,%s" % (
        base64.b64encode(buffer.getvalue()).decode('utf-8').replace('\n', '')
    )
    return image_data


def render_matplotlib(data, chart_type, info: dict):
    image_data = None
    plt.rcParams['axes.unicode_minus'] = False
    if chart_type == 'heatmap':
        columns = info['columns']
        data = data[columns]
        # define the colormap
        cmap = plt.get_cmap('PuOr')
        fig, ax = plt.subplots(figsize=(19, 15))
        heatmap = ax.imshow(data, interpolation="nearest", cmap=cmap)
        # cbar = plt.colorbar(heatmap)
        # Set the x- and y-axis ticks and labels
        # Add a colorbar to the heatmap
        fig.colorbar(heatmap, orientation='vertical', fraction=0.05)

        # ax.set_xticks(range(len(columns)))
        # ax.set_yticks(range(len(columns)))
        ax.set_xticklabels(columns, rotation=90)
        ax.set_yticklabels(columns, rotation=0)

        # Loop over data dimensions and create text annotations
        for i in range(len(columns) - 1):
            for j in range(len(columns) - 1):
                text = ax.text(
                    j,
                    i,
                    round(data.to_numpy()[i, j], 2),
                    ha="center",
                    va="center",
                    color="black"
                )

        # Save the heatmap as a PNG image in memory
        buffer = BytesIO()
        fig.savefig(buffer, format='png')
        plt.close(fig)
        # Convert the PNG image to a base64 encoded string
        # image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
        image_data = "data:image/png;charset=utf-8;base64,%s" % (
            base64.b64encode(buffer.getvalue()).decode('utf-8').replace('\n', '')
        )
    return image_data

class ReportWriter(AbstractWriter):
    mimetype: str = 'text/html'
    extension: str = '.html'
    ctype: str = 'html'
    download: bool = False
    output_format: str = 'iter'

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
        super(ReportWriter, self).__init__(
            request,
            resultset,
            filename=filename,
            response_type=response_type,
            download=download,
            compression=compression,
            ctype=ctype,
            **kwargs
        )
        try:
            self._chart_backend = kwargs['chart_backend']
        except KeyError:
            self._chart_backend = 'pygal'
        try:
            self._charts = kwargs['charts']
            del kwargs['charts']
            del self.kwargs['charts']
        except KeyError:
            self._charts = None
        if 'template' in kwargs:
            self._template = kwargs['template']
            del kwargs['template']
            if not self._template.endswith('html'):
                self._template = f"{self._template}.html"
        else:
            self._template = 'default.html'
        ### Then, Get the Template Parser:
        try:
            self.tpl = request.app['templating']
        except KeyError as ex:
            raise RuntimeError(
                f"Missing Jinja2 Template Engine for Reporting: {ex}"
            ) from ex

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        elif self.content_type == 'text/html':
            self.extension = '.html'
        else:
            self.extension = '.htm'
        return f"{dt}-{filename}{self.extension}"

    async def render_content(self) -> str:
        ## creating charts (if exists)
        charts = []
        if self._charts:
            for chart_name in self._charts:
                info = self._charts[chart_name]
                if self._chart_backend == 'pygal':
                    chart = pygal.Bar()  # TODO: making configurable from type
                    chart.title = chart_name
                    x = info['x_series']
                    chart.x_labels = [d[x] for d in self.data if x in d]
                    ## operate thorugh y-series:
                    for y in info['y_series']:
                        result = [d[y] for d in self.data if y in d]
                        chart.add(y, result)
                    chart = chart.render_data_uri()  # render bar chart
                    charts.append(chart)
                elif self._chart_backend == 'matplotlib':
                    try:
                        chart = render_matplotlib(self.data, chart_type=info['type'], info=info)
                        charts.append(chart)
                    except Exception:
                        pass
                elif self._chart_backend == 'seaborn':
                    try:
                        chart = render_seaborn(self.data, chart_type=info['type'], info=info)
                        charts.append(chart)
                    except Exception as exc:
                        print(exc)
                        pass

        ## parser params
        if isinstance(self.data, pandas.DataFrame):
            ## converting pandas to list of dict
            self.data = self.data.to_dict('records')
        params = {
            "resultset": self.data,
            "charts": charts,
            **self.kwargs
        }
        ### getting Template Parser
        try:
            return await self.tpl.render(
                template=self._template,
                params=params
            )
        except Exception as err:
            print(err)

    async def get_response(self) -> web.StreamResponse:
        buffer = await self.render_content()
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
