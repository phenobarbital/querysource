import time
from io import StringIO
from aiohttp import web
from .abstract import AbstractWriter

class XMLWriter(AbstractWriter):
    mimetype: str = 'application/xml'
    extension: str = '.xml'
    ctype: str = 'xml'
    download: bool = False
    output_format: str = None

    def get_filename(self, filename, extension: str = None):
        dt = time.time()
        # Use the provided extension or default to '.xml'
        if extension:
            self.extension = extension
        else:
            self.extension = '.xml'
        return f"{dt}-{filename}{self.extension}"

    async def get_response(self) -> web.StreamResponse:
        output = StringIO()
        # If self.data is a pandas DataFrame and supports to_xml,
        # convert it to XML. Otherwise, assume it's already a string.
        if hasattr(self.data, "to_xml"):
            # Convert the DataFrame to an XML string.
            # You can adjust parameters like index, root name, etc., as needed.
            self.data.to_xml(output, index=False)
        else:
            # Assume data is already a string containing XML.
            output.write(str(self.data))
        output.seek(0)
        buffer = output.getvalue()

        # Create the response using your pre-defined method.
        response = await self.response(self.response_type)
        content_length = len(buffer)
        response.content_length = content_length

        # If download is True, set a Content-Disposition header for file download.
        if self.download is True:
            response.headers['Content-Disposition'] = f"attachment; filename={self.filename}"
            await response.prepare(self.request)
            await response.write(bytes(buffer, 'utf-8'))
            await response.write_eof()
            return response
        else:
            return await self.stream_response(response, bytes(buffer, 'utf-8'))
