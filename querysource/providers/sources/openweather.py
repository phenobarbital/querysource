from typing import Any
from ...exceptions import QueryError, ConfigError
from .rest import restSource

class openweather(restSource):
    """
      openweather
        REST connector for OpenWeatherMap
    """

    base_url: str = 'http://api.openweathermap.org/'
    units: str = 'metric'
    days: int = 5

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        super(openweather, self).__post_init__(
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

        self._conditions = conditions

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError, KeyError):
            self.type = None

        if 'type' in kwargs:
            self.type = kwargs['type']
            del kwargs['type']

        if 'type' in conditions:
            self.type = conditions['type']
            del conditions['type']

        if 'days' in conditions:
            self._conditions['cnt'] = conditions['days']
            del conditions['days']
        else:
            self._conditions['cnt'] = self.days

        if 'units' not in self._conditions:
            self._conditions['units'] = self.units

        # Credentials
        if 'appid' not in conditions:
            self._conditions['appid'] = self._env.get('OPENWEATHER_APPID')
            if not self._conditions['appid']:
                try:
                    self._conditions['appid'] = definition.params['appid']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "OpenWeather: Missing Credentials"
                    ) from ex
        # type
        if self.type == 'weather':
            self.url = self.base_url + 'data/2.5/weather'
            ### getting data from lat-long
            if 'latitude' in self._conditions:
                self._conditions['lat'] = self._conditions['latitude']
            if 'longitude' in self._conditions:
                self._conditions['lon'] = self._conditions['longitude']
            if not self._conditions['lat']:
                ### getting geo-coding API
                q = []
                try:
                    city = self._conditions['city']
                    del self._conditions['city']
                    q.append(city)
                except KeyError as ex:
                    raise ValueError(
                        'OpenWeather: Missing City on parameters'
                    ) from ex
                try:
                    state = self._conditions['state_code']
                    del self._conditions['state_code']
                    q.append(state)
                except KeyError:
                    pass
                try:
                    country = self._conditions['country']
                    del self._conditions['country']
                    q.append(country)
                except KeyError:
                    pass
                self._conditions['q'] = ','.join(q)

    async def weather(self):
        self.url = self.base_url + 'data/2.5/weather'
        params = []
        if 'latitude' in self._conditions:
            self._conditions['lat'] = self._conditions['latitude']
            del self._conditions['latitude']
            try:
                self._conditions['lon'] = self._conditions['longitude']
                del self._conditions['longitude']
            except KeyError as ex:
                raise ConfigError(
                    'OpenWeather: Missing Longitude on parameters'
                ) from ex
        elif 'zipcode' in self._conditions:
            if 'country' in self._conditions:
                country = self._conditions['country']
                del self._conditions['country']
            else:
                country = 'us'
            # using zipcode:
            self._conditions['zip'] = f"{self._conditions['zipcode']},{country}"
            del self._conditions['zipcode']
        else:
            try:
                city = self._conditions['city']
                del self._conditions['city']
                params.append(city)
            except KeyError as ex:
                raise ValueError(
                    'OpenWeather: Missing City on parameters'
                ) from ex
            try:
                state = self._conditions['state_code']
                del self._conditions['state_code']
                params.append(state)
            except KeyError:
                pass
            try:
                country = self._conditions['country']
                del self._conditions['country']
                params.append(country)
            except KeyError:
                pass
            self._conditions['q'] = ','.join(params)
        try:
            self._result = await self.query()
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"OpenWeather: {ex}"
            ) from ex

    async def daily_forecast(self):
        self.url = self.base_url + 'data/2.5/forecast/daily'
        params = []
        if 'latitude' in self._conditions:
            self._conditions['lat'] = self._conditions['latitude']
            del self._conditions['latitude']
            try:
                self._conditions['lon'] = self._conditions['longitude']
                del self._conditions['longitude']
            except KeyError as ex:
                raise ConfigError(
                    'OpenWeather: Missing Longitude on parameters'
                ) from ex
        elif 'zipcode' in self._conditions:
            if 'country' in self._conditions:
                country = self._conditions['country']
                del self._conditions['country']
            else:
                country = 'us'
            # using zipcode:
            self._conditions['zip'] = f"{self._conditions['zipcode']},{country}"
            del self._conditions['zipcode']
        else:
            try:
                city = self._conditions['city']
                del self._conditions['city']
                params.append(city)
            except KeyError as ex:
                raise ValueError(
                    'OpenWeather: Missing City on parameters'
                ) from ex
            try:
                state = self._conditions['state_code']
                del self._conditions['state_code']
                params.append(state)
            except KeyError:
                pass
            try:
                country = self._conditions['country']
                del self._conditions['country']
                params.append(country)
            except KeyError:
                pass
            self._conditions['q'] = ','.join(params)
        try:
            self._result = await self.query()
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"OpenWeather: {ex}"
            ) from ex

    async def forecast(self):
        self.url = self.base_url + 'data/2.5/forecast'
        params = []
        if 'latitude' in self._conditions:
            self._conditions['lat'] = self._conditions['latitude']
            del self._conditions['latitude']
            try:
                self._conditions['lon'] = self._conditions['longitude']
                del self._conditions['longitude']
            except KeyError as ex:
                raise ConfigError(
                    'OpenWeather: Missing Longitude on parameters'
                ) from ex
        elif 'zipcode' in self._conditions:
            if 'country' in self._conditions:
                country = self._conditions['country']
                del self._conditions['country']
            else:
                country = 'us'
            # using zipcode:
            self._conditions['zip'] = f"{self._conditions['zipcode']},{country}"
            del self._conditions['zipcode']
        else:
            try:
                city = self._conditions['city']
                del self._conditions['city']
                params.append(city)
            except KeyError as ex:
                raise ValueError(
                    'OpenWeather: Missing City on parameters'
                ) from ex
            try:
                state = self._conditions['state_code']
                del self._conditions['state_code']
                params.append(state)
            except KeyError:
                pass
            try:
                country = self._conditions['country']
                del self._conditions['country']
                params.append(country)
            except KeyError:
                pass
            self._conditions['q'] = ','.join(params)
        try:
            self._result = await self.query()
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"OpenWeather: {ex}"
            ) from ex

    async def onecall(self):
        self.url = self.base_url + 'data/2.5/onecall'
        try:
            self._conditions['lat'] = self._conditions['latitude']
            del self._conditions['latitude']
        except KeyError as ex:
            raise ValueError(
                'OpenWeather: Missing Latitude on parameters'
            ) from ex
        try:
            self._conditions['lon'] = self._conditions['longitude']
            del self._conditions['longitude']
        except KeyError as ex:
            raise ValueError(
                'OpenWeather: Missing Longitude on parameters'
            ) from ex
        try:
            self._result = await self.query()
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"OpenWeather: {ex}"
            ) from ex

    async def pollution(self):
        self.url = self.base_url + 'data/2.5/air_pollution'
        try:
            self._conditions['lat'] = self._conditions['latitude']
            del self._conditions['latitude']
        except KeyError as ex:
            raise ValueError(
                'OpenWeather: Missing Latitude on parameters'
            ) from ex
        try:
            self._conditions['lon'] = self._conditions['longitude']
            del self._conditions['longitude']
        except KeyError as ex:
            raise ValueError(
                'OpenWeather: Missing Longitude on parameters'
            ) from ex
        try:
            self._result = await self.query()
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"OpenWeather: {ex}"
            ) from ex

    async def pollution_forecast(self):
        self.url = self.base_url + 'data/2.5/air_pollution/forecast'
        try:
            self._conditions['lat'] = self._conditions['latitude']
            del self._conditions['latitude']
        except KeyError as ex:
            raise ValueError(
                'OpenWeather: Missing Latitude on parameters'
            ) from ex
        try:
            self._conditions['lon'] = self._conditions['longitude']
            del self._conditions['longitude']
        except KeyError as ex:
            raise ValueError(
                'OpenWeather: Missing Longitude on parameters'
            ) from ex
        try:
            self._result = await self.query()
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"OpenWeather: {ex}"
            ) from ex
