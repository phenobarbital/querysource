from typing import Any
from .rest import restSource


class gmaps(restSource):
    base_url: str = 'https://maps.googleapis.com/maps/api/directions/json'

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError, KeyError):
            self.type = None

        try:
            self.type = self._conditions['type']
            del self._conditions['type']
        except (ValueError, AttributeError, KeyError):
            pass

        if 'type' in kwargs:
            self.type = kwargs['type']
            del kwargs['type']

        # Credentials
        if 'api_key' in self._conditions:
            self._conditions['key'] = self._conditions['api_key']
            del self._conditions['api_key']
        else:
            self._conditions['key'] = self._env.get('GOOGLE_API_KEY')
            if not self._conditions['key']:
                try:
                    self._conditions['key'] = definition.params['api_key']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "Google: Missing Credentials"
                    ) from ex

        # set parameters
        self._urlargs = {}

    async def route(self, data: dict = None):
        self.url = self.base_url
        try:
            self._result = await self.query(data=data)
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise
