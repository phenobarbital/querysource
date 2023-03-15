from datamodel import Field
from navconfig import config
from .rest import restDriver

upc_api_key = config.get('UPC_API_KEY')

# Validator from here: https://thepostulate.com/upc-validation-in-python-making-it-easy/

class upcDriver(restDriver):
    base_url: str = 'https://api.upcdatabase.org/'
    api_key: str = Field(required=True, default=upc_api_key)
    barcode: str = Field(required=False) # TODO: barcode UPC validator

    def product(self)-> dict:
        self.url = self.base_url + f'product/{self.barcode}'
        return {
            "url": self.url,
            "barcode": self.barcode
        }
