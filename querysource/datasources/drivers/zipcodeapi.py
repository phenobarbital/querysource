from dataclasses import asdict
from datamodel import Field
from navconfig import config
from .rest import restDriver


zipcode_key = config.get('ZIPCODE_API_KEY')

class zipcodeapiDriver(restDriver):
    access_token: str = Field(required=True, default=zipcode_key)
    url_schema: str = 'https://www.zipcodeapi.com/rest/{access_token}/'
    zipcode: str
    state: str
    radius: int
    units: str = Field(default='km')

    def by_units(self) -> dict:
        params = asdict(self)
        self.url_schema = self.url_schema + 'info.json/{zipcode}/{units}'
        self.url = self.url_schema.format(**params)
        return {
            "url": self.url
        }

    def by_zipcode(self) -> dict:
        params = asdict(self)
        self.url_schema = self.url_schema + 'city-zips.json/{city}/{state}'
        self.url = self.url_schema.format(**params)
        return {
            "url": self.url
        }

    def by_radius(self) -> dict:
        params = asdict(self)
        self.url_schema = self.url_schema + 'radius.json/{zipcode}/{radius}/{units}'
        self.url = self.url_schema.format(**params)
        return {
            "url": self.url
        }
