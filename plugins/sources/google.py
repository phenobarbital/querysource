import populartimes
import livepopulartimes
import time
import copy
from asyncdb.exceptions import ProviderError, NoDataFound
from querysource.exceptions import DriverError
from querysource.providers.rest import restSource


class google(restSource):
    """
      Google API
        Get all information from Google API (MAPS, geolocation, etc).
    """
    method: str = 'GET'

    def __init__(self, definition=None, params: dict = {}, **kwargs):
        self._place_id = ''
        super(google, self).__init__(definition, params, **kwargs)

        conditions = copy.deepcopy(params)

        try:
            self.type = definition.params['type']
        except (ValueError, KeyError):
            self.type = None

        if 'type' in params:
            self.type = params['type']
            del params['type']

        try:
            self.api_key = params['api_key']
            del params['api_key']
        except (ValueError, KeyError):
            try:
                self.api_key = definition.params['api_key']
            except (ValueError, AttributeError, KeyError):
                raise "Google: No API KEY defined"

        # get other params
        conditions['sensor'] = 'false'
        if self.type == 'textsearch':
            conditions['query'] = conditions['filter']
            try:
                del conditions['filter']
                del conditions['filterdate']
                del conditions['refresh']
                del conditions['place_id']
            except (KeyError, ValueError, TypeError):
                pass
            try:
                conditions['type'] = definition.params['store_type']
            except (KeyError, ValueError, TypeError):
                pass
            self.url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
        elif self.type == 'place_details':
            # Place ID
            if not 'place_id' in conditions and 'placeid' in conditions:
                conditions['place_id'] = conditions['placeid']
            try:
                self._place_id = conditions['place_id']
            except (ValueError, KeyError):
                raise
            try:
                del conditions['filter']
                del conditions['placeid']
            except (KeyError, ValueError, TypeError):
                pass
            self.url = 'https://maps.googleapis.com/maps/api/place/details/json'
        elif self.type == 'nearby':
            conditions['query'] = conditions['filter']
            try:
                del conditions['filter']
                del conditions['filterdate']
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            try:
                conditions['location'] = "{},{}".format(conditions['lat'], conditions['lng'])
                del conditions['lng']
                del conditions['lat']
            except (KeyError, ValueError, TypeError):
                pass
            try:
                conditions['radius'] = conditions['radius']
            except (KeyError, ValueError, TypeError):
                conditions['radius'] = 10000
            self.url = 'https://maps.googleapis.com/maps/api/place/textsearch/json'
        try:
            if conditions['required_fields']:
                conditions['fields'] = conditions['required_fields']
            del conditions['required_fields']
        except (KeyError, ValueError, TypeError):
            pass
        try:
            if definition.params['store_type']:
                conditions['type'] = definition.params['store_type']
        except (KeyError, ValueError, TypeError):
            pass
        # API key
        conditions['key'] = self.api_key
        self._params = conditions

        # create URL
        #self._url = self.build_url(self._url, args = None, queryparams = urlencode(conditions))

    async def get_next_result(self, result):
        r = []
        token = result['next_page_token']
        time.sleep(3)
        if self.type == 'textsearch':
            url = 'https://maps.googleapis.com/maps/api/place/textsearch/json?pagetoken={token}&key={key}'.format(token=token,key=self.api_key)
        elif self.type == 'nearby':
            url = 'https://maps.googleapis.com/maps/api/place/textsearch/json?pagetoken={token}&key={key}'.format(token=token,key=self.api_key)
        try:
            r = await self.request(url)
            if r["status"] and r["status"] == "OK":
                return r
            else:
                return []
        except Exception as err:
            print(err)
            return []


    async def query(self, url: str = None, params: dict = {}):
        result = await super(google, self).query(url, params)
        resultset = None
        try:
            if self.type == 'textsearch' or self.type == 'nearby':
                resultset = result['results']
                # TODO build the iteration based on next page token
                try:
                    #TODO making recursive queries
                    if result['next_page_token']:
                        r = await self.get_next_result(result)
                        resultset = [*resultset, *r["results"]]
                        if r['next_page_token']:
                            r = await self.get_next_result(r)
                            resultset = [*resultset, *r["results"]]
                except(KeyError, ValueError):
                    pass
                result = resultset
            elif self.type == 'place_details':
                result = result['result']
        except Exception as err:
            raise NoDataFound(f"Google Error: {err!s}")
        finally:
            return result
