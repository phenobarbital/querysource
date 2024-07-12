import time
from io import BytesIO
import pandas
from aiohttp import web
from .abstract import AbstractWriter

class ExcelWriter(AbstractWriter):
    mimetype: str = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    extension: str = '.xlsx'
    ctype: str = 'xlsx'
    output_format: str = 'pandas'

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        elif self.content_type == 'application/vnd.ms-excel':
            self.extension = '.xls'
        elif self.content_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
            self.extension = '.xlsx'
        elif self.content_type == 'application/vnd.ms-excel.sheet.macroEnabled.12':
            self.extension = '.xlsm'
        elif self.content_type == 'application/vnd.oasis.opendocument.spreadsheet':
            self.extension = '.ods'
        else:
            self.extension = '.xlsx'
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        output = BytesIO()

        # Ensure all datetime columns are timezone naive
        for col in self.data.select_dtypes(include=['datetimetz']).columns:
            self.data[col] = self.data[col].dt.tz_convert(None)

        # Engine Output
        if self.extension == '.xlsx':
            engine = 'xlsxwriter'
        elif self.extension == '.xlsm':
            engine = 'openpyxl'
        elif self.extension == '.ods':
            engine = 'odf'
        else:
            engine = 'xlrd'
        # create the engine
        columns = list(self.data.columns)
        with pandas.ExcelWriter(output, engine=engine) as writer:  # pylint: disable=E0110
            self.data.to_excel(
                writer,
                sheet_name="Sheet1",
                columns=columns,
                header=True,
                index=False
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
