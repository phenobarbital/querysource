from abc import ABC
from typing import Any
from datamodel.parsers.json import json_encoder
from ...exceptions import (
    DriverError,
    QueryError,
    DataNotFound,
    ConfigError
)
from .rest import restSource


class graphqlSource(restSource, ABC):
    """
    Abstract Class for creating GraphQL-based APIs.
    """
    url: str = ''
    api_name: str = 'GraphQL'
    auth_type: str = 'apikey'
    auth_key_name: str = 'apikey'
    token_type: str = 'Bearer'
    env_key: str = ''
    method: str = 'post'  # All calls will be POST
    data_format: str = 'json'
    accept: str = "*/*"
    gql: str = '''
    query {{
        {operation} {{
            {fields}
        }}
    }}
    '''
    operation: str = None
    fields: str = None
    _operation: str = None

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        self._query: dict = {}
        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in self._conditions:
            self.type = self._conditions.pop('type')

        if 'type' in kwargs:
            self.type = kwargs.pop('type')

        if self.auth_type == 'apikey':
            if 'apikey' in self._conditions:
                self.auth[self.auth_key_name] = self._conditions['apikey']
                del self._conditions['apikey']
            else:
                self.auth[self.auth_key_name] = self._env.get(self.env_key)
                if not self.auth[self.auth_key_name]:
                    try:
                        self.auth[self.auth_key_name] = definition.params['api_key']
                    except (ValueError, AttributeError) as ex:
                        raise ConfigError(
                            f"{self.api_name}: Missing API Key"
                        ) from ex

    def encode_string(self, data: Any) -> str:
        return json_encoder(data)

    async def query(self, data: str = None):
        result = None
        if not data:
            data = self._query
        if not data:
            raise DriverError(
                f"{self.api_name}: Query is missing"
            )
        try:
            result, error = await self.request(
                self.url,
                self.method,
                data=data
            )
            if not result:
                raise DataNotFound(
                    f"{self.api_name}: No Data was found: {error}".format(error)
                )
            elif error:
                raise DriverError(str(error))
            elif 'errors' in result:
                raise DriverError(f"{result['errors']}")
        except DataNotFound:
            raise
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"{self.api_name}: {ex}"
            ) from ex
        # if result then
        try:
            result = result['data'][self._operation]
        except (ValueError, KeyError) as ex:
            raise QueryError(
                f'{self.api_name}: Incorrect Data result format: {ex}'
            ) from ex
        self._result = result
        return result
