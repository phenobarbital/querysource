from typing import Any
from datetime import date
from urllib.parse import urlencode
from ...exceptions import DriverError
from .rest import restSource

class retailnext(restSource):
    """
      RetailNext
        get Traffic information about stores
    """
    base_url: str = 'https://{subscription}.retailnext.net/v1/'
    method: str = 'get'
    _query = {}
    location = None

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in self._conditions:
            self.type = self._conditions['type']
            del self._conditions['type']

        if 'api_key' in self._conditions:
            self.api_key = self._conditions['api_key']
            del self._conditions['api_key']
        else:
            try:
                self.api_key = definition.params['api_key']
            except (ValueError, KeyError) as ex:
                raise DriverError(
                    "RetailNext: No API KEY defined"
                ) from ex

        if 'subscription' in self._conditions:
            self.subscription = self._conditions['subscription']
            del self._conditions['subscription']
        else:
            try:
                self.subscription = definition.params['subscription']
            except (ValueError, KeyError):
                self.subscription = 'samsung-us.api'

        self._args['subscription'] = self.subscription

        try:
            self.location = self._conditions['location']
            del self._conditions['location']
        except (ValueError, KeyError):
            self.location = None

        if self.type == 'locations':
            self.url = 'https://{subscription}.retailnext.net/v1/location'
        elif self.type == 'traffic':
            self.method = 'POST'
            self.url = 'https://{subscription}.retailnext.net/v1/datamine'
            self._query = {
                "metrics": [
                    "traffic_in",
                    "traffic_out"
                ],
                "date_ranges": [{
                    "last_day": self._conditions['last_day'],
                    "first_day": self._conditions['first_day']
                }],
                "group_bys": [{
                    "unit": "minutes",
                    "group": "time",
                    "value": 15
                }, {"group": "location",
                    "value": 1,
                    "unit": ""
                    }],
                "time_ranges": [{
                    "type": "store_hours"
                }],
                "locations": []
            }
            del self._conditions['first_day']
            del self._conditions['last_day']

    def structData(self, data):
        try:
            first_day = self._query['date_ranges'][0]['first_day']
            last_day = self._query['date_ranges'][0]['last_day']
        except (KeyError, ValueError):
            first_day = date.today().isoformat()
            last_day = date.today().isoformat()
        lista = [
            {
                "first_day": first_day,
                "last_day": last_day,
                "type": dat['name'],
                "time start": item['group']['start'],
                "time end": item['group']['finish'],
                "location": element['group']['uuid'],
                "location name": element['group']['name'],
                "traffic": element['value']
            } for dat in data['metrics'] for item in dat['data'] for element in item['next_level']
        ]
        return lista

    async def query(self, url: str = None, params: dict = None):
        if url:
            self.url = self.build_url(url, queryparams=urlencode(params))
        else:
            # create URL
            self.url = self.build_url(
                self.url,
                args=self._urlargs,
                queryparams=urlencode(self._conditions)
            )
        try:
            if self.type == 'traffic':
                if not self.location:
                    # first: i need the location of all stores
                    uri = f'https://{self.subscription}.retailnext.net/v1/location'
                    result = await self.request(uri, method='get')
                    locations = [k['id'] for k in result['locations'] if k["type"] == 'store']
                    locations = list(set(locations))
                else:
                    if isinstance(self.location, str):
                        locations = [self.location]
                    else:
                        locations = self.location
                self._query['locations'] = locations
                result = await self.request(
                    self.url, self.method, data=self._query
                )
                if result:
                    self._result = self.structData(result)
            elif self.type == 'locations':
                self._result = await self.request(
                    self.url, 'get'
                )
            return self._result
        except Exception as err:
            print(err)
            self._result = {
                "exception": err,
                "error": str(err)
            }
            raise DriverError(
                f"Error query Retailnext API: {err}"
            ) from err
