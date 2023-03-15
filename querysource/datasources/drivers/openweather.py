"""OpenWeatherMap Datasource definition.
"""
from datamodel import Field
from navconfig import config
from .abstract import CloudDriver


appid = config.get('OPENWEATHER_APPID')

class openweatherDriver(CloudDriver):
    driver: str = 'openweather'
    base_url: str = Field(default='http://api.openweathermap.org/')
    appid: str = Field(required=True, default=appid)
    city: str = Field(required=False, default=None)
    state_code: str = Field(required=False, default=None)
    country: str = Field(required=False, default=None)
    latitude: float = Field(required=False, default=None)
    longitude: float = Field(required=False, default=None)

    def weather(self) -> dict:
        params = []
        self.url = self.base_url + 'data/2.5/weather'
        if self.city:
            params.append(self.city)
        if self.country:
            params.append(self.country)
        if self.state_code:
            params.append(self.state_code)
        return {
            "url": self.url,
            "q": ",".join(params)
        }

    def daily_forecast(self) -> dict:
        params = []
        self.url = self.base_url + 'data/2.5/forecast/daily'
        if self.city:
            params.append(self.city)
        if self.country:
            params.append(self.country)
        if self.state_code:
            params.append(self.state_code)
        return {
            "url": self.url,
            "q": ",".join(params)
        }

    def forecast(self) -> dict:
        self.url = self.base_url + 'data/2.5/onecall'
        params = {}
        params['lat'] = self.latitude
        params['lon'] = self.longitude
        return {
            "url": self.url,
            "q": ",".join(params)
        }
