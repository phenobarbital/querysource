from typing import Any
from .rest import restSource

class countries(restSource):
    """
      countries
        REST connector REST Countries (countries information)
    """
    method: str = 'get'
    base_url: str = 'https://restcountries.com/v3.1/'

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
            self.type = conditions['type']
            del conditions['type']
        except (ValueError, AttributeError, KeyError):
            pass

        if 'type' in kwargs:
            self.type = kwargs['type']
            del kwargs['type']

        if 'fields' in self._conditions:
            if isinstance(self._conditions['fields'], list):
                self._conditions['fields'] = ','.join(self._conditions['fields'])

        # set parameters
        self._args = conditions.copy()

    async def all(self):
        """all.

        Returns the list of all countries in the world
        """
        self.url = self.base_url + 'all'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def country(self, country: str = None):
        """country.

        Get Country Information.
        """
        try:
            if self._request:
                for key in self._request.match_info:
                    if key == 'var':
                        self._args['country'] = self._request.match_info.get(key)
        except Exception as err:
            print(err)
            self.logger.error(err)
        if country:
            self._args['country'] = country
        elif 'country' in self._conditions:
            self._args['country'] = self._conditions['country']
            del self._conditions['country']
        if 'country' not in self._args:
            raise ValueError(
                'Countries API: Missing Country in API'
            )
        self.url = self.base_url + 'name/{country}'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def code(self, code: str = None):
        """code.

        Get Country Information by country code.
        """
        try:
            if self._request:
                for key in self._request.match_info:
                    if key == 'var':
                        self._args['country'] = self._request.match_info.get(key)
        except Exception as err:
            print(err)
            self.logger.error(err)
        if code:
            self._args['country'] = code
        elif 'country' in self._params:
            self._args['country'] = self._conditions['country']
            del self._conditions['country']
        if 'country' not in self._args:
            raise ValueError(
                'Countries API: Missing Country Code in API'
            )
        self.url = self.base_url + 'alpha/{country}'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise
