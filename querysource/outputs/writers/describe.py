from typing import Any, Union
from aiohttp import web
from navconfig.logging import logging
from .abstract import AbstractWriter

log = logging.getLogger('great_expectations')
log.setLevel(logging.WARNING)

log = logging.getLogger('great_expectations.experimental.datasources')
log.setLevel(logging.WARNING)

log = logging.getLogger('great_expectations.experimental.datasources')
log.setLevel(logging.WARNING)

import great_expectations as ge
from great_expectations import DataContext


class DescribeWriter(AbstractWriter):
    mimetype: str = 'application/json'
    extension: str = '.json'
    ctype: str = 'json'
    output_format: str = 'pandas' # TODO: adding arguments to output format

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
        super(DescribeWriter, self).__init__(
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
        if 'expectations' in kwargs:
            self._enable_expectations: bool = kwargs['expectations']
            del kwargs['expectations']
        else:
            self._enable_expectations: bool = False

    async def get_response(self) -> web.StreamResponse:
        # Create a Great Expectations DataContext
        # context = DataContext()
        # suite = f"{self.filename}_suite"
        # # Create an Expectation Suite
        # context.create_expectation_suite(suite, overwrite_existing=True)
        # batch_kwargs = {"datasource": suite, "dataset": self.data}
        # batch = context.get_batch(batch_kwargs=batch_kwargs, expectation_suite_name=suite)
        if self._enable_expectations is True:
            gedf = ge.dataset.PandasDataset(self.data)
        ### making describe and calculations about dataset statistics
        cat = self.data.select_dtypes(include=['object', 'string'])
        categorical = {}
        ### getting categorical describe
        for col in cat.columns:
            info = self.data[col].describe()
            categorical[f'{col}'] = {
                "describe": info.to_dict(),
                "values": list(self.data[col].unique()),
                "frequency": list(self.data[col].value_counts()),
                "type": "categorical"
            }
            if self._enable_expectations is True:
                categorical[f'{col}']['expectations'] = {
                    "not_null": gedf.expect_column_values_to_not_be_null(column=col,  result_format={'result_format': 'COMPLETE'}).to_json_dict(),
                    "to_be_unique": gedf.expect_column_values_to_be_unique(column=col,  result_format={'result_format': 'COMPLETE'}).to_json_dict(),
                    "between": gedf.expect_column_value_lengths_to_be_between(
                        column=col, min_value=1, max_value=100, mostly=.99,  result_format={'result_format': 'COMPLETE'}
                    ).to_json_dict(),
                }
        # at now, the others:
        num = self.data.select_dtypes(exclude=['object', 'string'])
        numbers = {}
        for col in num.columns:
            info = self.data[col].describe()
            numbers[col] = {
                "describe": info.to_dict(),
                "type": "numeric"
            }
            if self._enable_expectations is True:
                numbers[f'{col}']['expectations'] = {
                    "between": gedf.expect_column_values_to_be_between(
                        column=col, min_value=-10, max_value=10000,  result_format={'result_format': 'COMPLETE'}
                    ).to_json_dict(),
                }
        result = {
            "uniques": self.data.nunique().to_dict(),
            "columns": {**categorical, **numbers }
        }
        try:
            data = self._json.dumps(result)
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
            self.logger.debug('::: SENDING DESCRIBE RESPONSE: ')
            return response
        else:
            data = bytes(data, 'utf-8')
            response = await self.response(self.response_type)
            content_length = len(data)
            response.content_length = content_length
            if self.download is True: # inmediately download response
                await response.prepare(self.request)
                await response.write(data)
                await response.write_eof()
                return response
            return await self.stream_response(response, data)
