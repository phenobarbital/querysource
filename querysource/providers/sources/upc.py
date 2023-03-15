from typing import Any
from .rest import restSource


class upc(restSource):
    """
      upc
        REST connector for UPC barcodes database
    """

    base_url: str = 'https://api.upcdatabase.org/'
    _apikey: str = ''
    method: str = 'get'

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        print('UPC CONDITIONS> ', conditions, kwargs)

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
        if 'key' in self._conditions:
            self.auth['apikey'] = self._conditions['key']
            del self._conditions['key']
        elif 'apikey' in self._conditions:
            self.auth['apikey'] = self._conditions['apikey']
            del self._conditions['apikey']
        else:
            self.auth['apikey'] = self._env.get('UPC_API_KEY')
            if not self.auth['apikey']:
                try:
                    self.auth['apikey'] = definition.params['apikey']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "UPC Database: Missing Credentials"
                    ) from ex

        # set parameters
        self._args = conditions.copy()

        if 'method' in self._conditions:
            del self._conditions['method']

    async def product(self, barcode: str = None):
        """product.

        Product information based on UPC barcode
        """
        # if not units
        if barcode:
            self._conditions['barcode'] = barcode
        try:
            self._args['barcode'] = self._conditions['barcode']
            del self._conditions['barcode']
        except (ValueError, KeyError, AttributeError) as ex:
            raise ValueError(
                "UPC Database: Missing Barcode"
            ) from ex
        self.url = self.base_url + 'product/{barcode}'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def currency(self):
        """currency.

        Currency information and exchange rates supported by UPC
        """
        self.url = self.base_url + 'currency/latest/'
        self._conditions['base'] = 'USD'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise
