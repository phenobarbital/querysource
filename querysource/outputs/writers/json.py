from io import BytesIO, StringIO

from aiohttp import web
from .abstract import AbstractWriter


class jsonWriter(AbstractWriter):
    mimetype: str = 'application/json'
    extension: str = '.json'
    ctype: str = 'json'
    output_format: str = 'iter'

    async def get_response(self) -> web.StreamResponse:
        try:
            from pandas import DataFrame
            is_dataframe = isinstance(self.data, DataFrame)
        except ImportError:
            DataFrame = None  # type: ignore
            is_dataframe = False
        # Multi-source pipelines without an operator (no Join/Concat/Melt/Merge)
        # return a dict of DataFrames (one per source). Serialize each frame to
        # records so the frontend can render per-source tabs.
        is_multi_df = (
            DataFrame is not None
            and isinstance(self.data, dict)
            and len(self.data) > 0
            and all(isinstance(v, DataFrame) for v in self.data.values())
        )
        if is_dataframe:
            # Convert to a list of dictionaries
            data_dict = self.data.to_dict(orient='records')
            data = self._json.dumps(data_dict)
        elif is_multi_df:
            data_dict = {
                key: frame.to_dict(orient='records')
                for key, frame in self.data.items()
            }
            data = self._json.dumps(data_dict)
        else:
            try:
                data = self._json.dumps(self.data)
            except ValueError as ex:
                return self.error(
                    message=f"Error parsing JSON Data: {ex}",
                    exception=ex,
                    status=500
                )
            except Exception:  # pylint: disable=W0706
                raise
        ### calculating the different responses:
        if self.response_type == 'web':
            response = await self.response(self.response_type, data)
            self.logger.debug('::: SENDING WEB JSON RESPONSE: ')
            return response
        else:
            if not isinstance(data, bytes):
                data = bytes(data, 'utf-8')
            response = await self.response(self.response_type)
            content_length = len(data)
            response.content_length = content_length
            if self.download is True:  # inmediately download response
                await response.prepare(self.request)
                await response.write(data)
                await response.write_eof()
                return response
            return await self.stream_response(response, data)
