import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Optional, Union

from aiohttp import web

# from reportlab.lib.pagesizes import letter, A4
# from reportlab.platypus import SimpleDocTemplate, Paragraph

from ...conf import PDF_RENDER_WORKERS
from .report import ReportWriter


_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _pdf_executor() -> ThreadPoolExecutor:
    """Return the process-wide PDF render executor, creating it on first use.

    Bounded to ``PDF_RENDER_WORKERS`` threads (prefix ``qs-pdf``) so parallel
    WeasyPrint renders cannot grow memory without limit. Loop-agnostic: no
    ``asyncio.Semaphore``, so it is safe across event loops.

    Returns:
        ThreadPoolExecutor: the shared executor instance.
    """
    global _EXECUTOR  # noqa: PLW0603 -- process-wide lazy singleton
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=PDF_RENDER_WORKERS,
            thread_name_prefix="qs-pdf",
        )
    return _EXECUTOR


def _render_pdf(html: str) -> bytes:
    """Render ``html`` to PDF bytes with WeasyPrint (sync, CPU-bound).

    Runs entirely in the worker thread: the ``weasyprint`` import (the first
    call pays ~0.45 s), the ``HTML(string=html)`` build and ``write_pdf`` all
    happen here, so none of them touch the event loop.

    Args:
        html: the rendered report HTML.

    Returns:
        bytes: the PDF document.
    """
    from weasyprint import HTML

    output = BytesIO()
    HTML(string=html).write_pdf(output)
    return output.getvalue()


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
        super().__init__(
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
        self.pdf_library = kwargs.pop('library', 'weasyprint')

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        if extension:
            self.extension = extension
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        """Render content, build the PDF on the ``qs-pdf`` executor, then download or stream it.

        Exceptions raised by ``_render_pdf`` propagate unchanged, into
        ``DataOutput.response``'s existing writer-error handling. Behaviour after
        the render (content_length, Content-Disposition, download vs
        stream_response) is unchanged.
        """
        result = await self.render_content()
        response = await self.response(self.response_type)
        # Create the PDF off the event loop, on the bounded qs-pdf executor.
        buffer = await asyncio.get_running_loop().run_in_executor(
            _pdf_executor(), _render_pdf, result
        )
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
