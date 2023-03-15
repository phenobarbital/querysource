import time
from typing import Any, Union
from io import BytesIO
from aiohttp import web
# from reportlab.lib.pagesizes import letter, A4
# from reportlab.platypus import SimpleDocTemplate, Paragraph
from weasyprint import HTML
from .report import ReportWriter

class PDFWriter(ReportWriter):
    mimetype: str = 'application/pdf'
    extension: str = '.pdf'
    ctype: str = 'pdf'
    download: bool = True
    output_format: str = 'iter'
    pdf_library: str = 'reportlab'

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
        super(PDFWriter, self).__init__(
            request,
            resultset,
            filename=filename,
            response_type=response_type,
            download=download,
            compression=compression,
            ctype=ctype,
            **kwargs
        )
        ### check if can change pdf library:
        if 'library' in kwargs:
            self.pdf_library = kwargs['library']
            del kwargs['library']

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        output = BytesIO()
        result = await self.render_content()
        response = await self.response(self.response_type)
        # Create the PDF.
        HTML(string=result).write_pdf(output)
        output.seek(0)
        buffer = output.getvalue()
        # if self.download is True: # inmediately download response
        content_length = len(buffer)
        response.content_length = content_length
        if self.download is True: # inmediately download response
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(buffer)
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, buffer)
