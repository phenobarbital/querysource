from typing import Any
from navconfig.logging import logging
from .rest import restSource

class geofcc(restSource):
    """
      Geo FCC
        API for get information about Census FCC
    """
    base_url: str = 'https://geo.fcc.gov/api/census/'
    year: int = 2020

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

        if 'year' in conditions:
            self.year = conditions['year']

        # set parameters
        self._args = conditions
        self._args['year'] = self.year
        self._conditions = {}

        if self.type == 'block':
            self._params = {}
            self.url = self.base_url + 'block/find?latitude={latitude}&longitude={longitude}&censusYear={year}&showall=true&format=json'

        elif self.type == 'area':
            self.url = self.url + 'area?format=json'

    async def block(self):
        self._params = {}
        self.url = self.base_url + 'block/find?latitude={latitude}&longitude={longitude}&censusYear={year}&showall=true&format=json'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def area(self):
        self.url = self.base_url + 'area?lat={latitude}&lon={longitude}&censusYear={year}&format=json'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise
