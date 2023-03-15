from typing import Any
from .rest import restSource


class zipcodeapi(restSource):
    """
      ZipCodeAPI
        api for querying RESTful service for US ZIP Codes
    """
    base_url: str = 'https://www.zipcodeapi.com/rest/{api_key}/'
    _units: str = 'km'

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        print('ZIP CONDITIONS> ', conditions, kwargs)

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in conditions:
            self.type = conditions['type']
            del conditions['type']

        # Credentials: TODO: pass to a function called "get_credentials"
        if 'key' in self._conditions:
            self._conditions['api_key'] = self._conditions['key']
            del self._conditions['key']
        if not 'api_key' in conditions:
            self._conditions['api_key'] = self._env.get('ZIPCODE_API_KEY')
            if not self._conditions['api_key']:
                try:
                    self._conditions['api_key'] = definition.params['api_key']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "ZipcodeAPI: Missing Credentials"
                    ) from ex

        if self.type == 'units':
            self.url = self.base_url + 'info.json/{zipcode}/{units}'
            self._conditions['units'] = 'degrees'
        elif self.type == 'zipcode':
            self.url = self.base_url + 'city-zips.json/{city}/{state}'
        elif self.type == 'radius':
            self.url = self.base_url + 'radius.json/{zipcode}/{radius}/{units}'
            self._conditions['units'] = 'km'

        self._args = self._conditions.copy()
        self._conditions = {}


    async def units(self, zipcode: str = None):
        """units.

        Zipcode to Location information.
        """
        self._conditions = {}
        if zipcode:
            self._args['zipcode'] = zipcode
        self._args['units'] = 'degrees'
        # if not units
        if 'units' not in self._args:
            self._args['units'] = self._units
        self.url = self.base_url + 'info.json/{zipcode}/{units}'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def radius(self, zipcode: str = None, radius: int = 5):
        """radius.

        Find all zip codes within a given radius of a zip code.
        """
        self._conditions = {}
        if zipcode:
            self._args['zipcode'] = zipcode
            self._args['units'] = 'km'
            self._args['radius'] = radius
        # if not units
        if 'units' not in self._args:
            self._args['units'] = self._units
        self.url = self.base_url + 'radius.json/{zipcode}/{radius}/{units}'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def zipcode(self, city: str = None, state: str = None):
        """zipcode.

        Zipcode from City and State.
        """
        self._conditions = {}
        if city:
            self._args['city'] = city
            self._args['state'] = state
        self.url = self.base_url + 'city-zips.json/{city}/{state}'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise
