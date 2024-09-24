"""OpenWeatherMap Datasource definition.
"""
from datamodel import Field
from navconfig import config
from .abstract import CloudDriver


appid = config.get('OPENWEATHER_APPID')

class countriesDriver(CloudDriver):
    driver: str = 'countries'
    base_url: str = Field(default='https://restcountries.com/v3.1/')
    country: str = Field(required=False, default=None)

    def all(self) -> dict:
        self.url = self.base_url + 'all'
        return {
            "url": self.url
        }

    def get_country(self) -> dict:
        """country.

        Get Country Information.
        """
        self.url = self.base_url + f'name/{self.country}'
        return {
            "url": self.url
        }
