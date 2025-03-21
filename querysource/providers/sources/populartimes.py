from typing import Any
import asyncio
import re
import urllib
import orjson
from .rest import restSource
from ...exceptions import ConfigError, DriverError


class populartimes(restSource):
    """
        populartimes.
        Using Google API for extracting information about Places atmosphere.
        sends request to Google Maps detail API to get a search string
        and uses standard proto buffer to get additional information
        on the current status of popular times.
    """
    base_url: str = 'https://maps.googleapis.com/maps/api/place/'
    use_proxies: bool = True
    rotate_ua: bool = True

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        self.rotate_ua: bool = True

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if not self.type:
            self.type = conditions.pop('type', 'by_placeid')

        # Place Id:
        self.place_id = conditions.get('place_id', None)
        self.logger.notice(f'Place Id: {self.place_id}')

        # Credentials: TODO: pass to a function called "get_credentials"
        if 'key' in self._conditions:
            self._conditions['api_key'] = self._conditions['key']
            del self._conditions['key']
        if 'api_key' not in conditions:
            self._conditions['api_key'] = self._env.get('GOOGLE_PLACES_API_KEY')
            if not self._conditions['api_key']:
                try:
                    self._conditions['api_key'] = definition.params['api_key']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "Google Populartimes: Missing Credentials"
                    ) from ex

        if self.type == 'by_placeid':
            self.url = self.base_url + 'details/json?placeid={place_id}&key={api_key}'

        self._args = self._conditions.copy()
        self._conditions = {}

    async def rating_reviews(self, place_id: str = None, api_key: str = None, **kwargs):
        """rating_reviews.
        get the rating and reviews of a place.
        :param api_key: api key
        :param place_id: unique place_id from google
        :return: json details
        """
        self._conditions = {}
        self._args['place_id'] = self.place_id
        if not self._args['place_id']:
            raise ConfigError(
                "Populartimes Error: no placeid provided."
            )
        if not api_key:
            self._args['api_key'] = self._env.get('GOOGLE_PLACES_API_KEY')
        # Places by ID
        self.url = self.base_url + 'details/json?fields=rating%2Creviews%2Cuser_ratings_total&placeid={place_id}&key={api_key}'
        try:
            query = await self.aquery()
            self.check_response_code(query)
            result = query.get('result')
            self._result = result
            return self._result
        except Exception as err:
            self.logger.error(err)
            raise

    async def by_placeid(self, place_id: str = None, api_key: str = None, **kwargs):
        """by_placeid.
        get the current status of popular times.
        :param api_key: api key
        :param place_id: unique place_id from google
        :return: json details
        """
        self._conditions = {}
        self._args['place_id'] = self.place_id
        if not self._args['place_id']:
            raise ConfigError(
                "Populartimes Error: no placeid provided."
            )
        if not api_key:
            self._args['api_key'] = self._env.get('GOOGLE_PLACES_API_KEY')
        # Places by ID
        self.url = self.base_url + 'details/json?placeid={place_id}&key={api_key}'
        try:
            query = await self.aquery()
            self.check_response_code(query)
            result = query.get('result')
            address = result['name'] + ', ' + result["formatted_address"] if "formatted_address" in result else result.get("vicinity", "")
            data = {}
            popular_times = None
            try:
                pdata = await self.make_google_search(address)
            except ValueError:
                pdata = None
            if pdata:
                data = self.get_populartimes(result, pdata)
                popular_times = data['popular_times']
            # Convert popular_times into a dictionary
            if popular_times is not None:
                popular_times = {str(item[0]): item[1] for item in popular_times}
                for k, v in popular_times.items():
                    new_dict = {}
                    for traffic in v:
                        hour = str(traffic[0])
                        new_dict[hour] = {
                            "human_hour": traffic[4],
                            "traffic": traffic[1],
                            "traffic_status": traffic[2]
                        }
                    popular_times[k] = new_dict
                data['popular_times'] = popular_times
            # Merge places and popular times
            result.update(data)
            self._result = result
            return self._result
        except Exception as err:
            self.logger.error(err)
            raise

    def index_get(self, array, *argv):
        """
        checks if a index is available in the array and returns it
        :param array: the data array
        :param argv: index integers
        :return: None if not available or the return value
        """

        try:
            for index in argv:
                array = array[index]
            return array
        # there is either no info available or no popular times
        # TypeError: rating/rating_n/populartimes wrong of not available
        except (IndexError, TypeError):
            return None

    def get_populartimes(self, result, data):
        # get info from result array, has to be adapted if backend api changes
        info = self.index_get(data, 0, 1, 0, 14)
        rating = self.index_get(info, 4, 7)
        rating_n = self.index_get(info, 4, 8)
        popular_times = self.index_get(info, 84, 0)
        # current_popularity is also not available if popular_times isn't
        current_popularity = self.index_get(info, 84, 7, 1)
        time_spent = self.index_get(info, 117, 0)
        # extract wait times and convert to minutes
        if time_spent:
            nums = [float(f) for f in re.findall(r'\d*\.\d+|\d+', time_spent.replace(",", "."))]
            contains_min, contains_hour = "min" in time_spent, "hour" in time_spent or "hr" in time_spent
            time_spent = None
            if contains_min and contains_hour:
                time_spent = [nums[0], nums[1] * 60]
            elif contains_hour:
                time_spent = [nums[0] * 60, (nums[0] if len(nums) == 1 else nums[1]) * 60]
            elif contains_min:
                time_spent = [nums[0], nums[0] if len(nums) == 1 else nums[1]]

            time_spent = [int(t) for t in time_spent]

        return {
            "rating": rating,
            "rating_n": rating_n,
            "current_popularity": current_popularity,
            "popular_times": popular_times,
            "time_spent": time_spent
        }

    def check_response_code(self, response):
        """
        check if query quota has been surpassed or other errors occured
        :param resp: json response
        :return:
        """
        if response["status"] == "OK" or response["status"] == "ZERO_RESULTS":
            return

        if response["status"] == "REQUEST_DENIED":
            raise DriverError(
                "Google Places " + response["status"],
                "Request was denied, the API key is invalid."
            )

        if response["status"] == "OVER_QUERY_LIMIT":
            raise DriverError(
                "Google Places " + response["status"],
                "You exceeded your Query Limit for Google Places API Web Service, "
                "check https://developers.google.com/places/web-service/usage "
                "to upgrade your quota."
            )

        if response["status"] == "INVALID_REQUEST":
            raise DriverError(
                "Google Places " + response["status"],
                "The query string is malformed, "
                "check if your formatting for lat/lng and radius is correct."
            )

        if response["status"] == "NOT_FOUND":
            raise DriverError(
                "Google Places " + response["status"],
                "The place ID was not found and either does not exist or was retired."
            )

        raise DriverError(
            "Google Places " + response["status"],
            "Unidentified error with the Places API, please check the response code"
        )

    async def make_google_search(self, query_string: str):
        params_url = {
            "tbm": "map",
            "tch": 1,
            "hl": "en",
            "q": urllib.parse.quote_plus(query_string),
            "pb": "!4m12!1m3!1d4005.9771522653964!2d-122.42072974863942!3d37.8077459796541!2m3!1f0!2f0!3f0!3m2!1i1125!2i976"
            "!4f13.1!7i20!10b1!12m6!2m3!5m1!6e2!20e3!10b1!16b1!19m3!2m2!1i392!2i106!20m61!2m2!1i203!2i100!3m2!2i4!5b1"
            "!6m6!1m2!1i86!2i86!1m2!1i408!2i200!7m46!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e3!2b0!3e3!"
            "1m3!1e4!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e3!2b1!3e2!1m3!1e9!2b1!3e2!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e"
            "10!2b0!3e4!2b1!4b1!9b0!22m6!1sa9fVWea_MsX8adX8j8AE%3A1!2zMWk6Mix0OjExODg3LGU6MSxwOmE5ZlZXZWFfTXNYOGFkWDh"
            "qOEFFOjE!7e81!12e3!17sa9fVWea_MsX8adX8j8AE%3A564!18e15!24m15!2b1!5m4!2b1!3b1!5b1!6b1!10m1!8e3!17b1!24b1!"
            "25b1!26b1!30m1!2b1!36b1!26m3!2m2!1i80!2i92!30m28!1m6!1m2!1i0!2i0!2m2!1i458!2i976!1m6!1m2!1i1075!2i0!2m2!"
            "1i1125!2i976!1m6!1m2!1i0!2i0!2m2!1i1125!2i20!1m6!1m2!1i0!2i956!2m2!1i1125!2i976!37m1!1e81!42b1!47m0!49m1"
            "!3b1"
        }
        search_url = "http://www.google.com/search?" + "&".join(k + "=" + str(v) for k, v in params_url.items())
        self.logger.debug(f':: SEARCH URL {search_url} ')

        try:
            response, _ = await self.http_request(search_url, 'get', use_json=False, use_proxies=True)
            await asyncio.sleep(0.5)
            if not response:
                raise ValueError(
                    "Unable to get Google Search"
                )
            if response.status_code == 429:
                # try to use selenium request:
                response = await self.selenium_request(search_url, 'get')
                if response is None:
                    error = await response.aread()
                    self.logger.error(
                        "Google Search: Too many requests"
                    )
                    self.logger.error(f"Raw response Error: {error}")
                    return None
                result = response
            elif response.status_code > 299:
                error = await response.aread()
                self.logger.error(f"Raw response Error: {error}")
                return None
            else:
                result = await response.aread()
                result = result.decode('utf-8')
            # Decode response and ensure it's not empty
            data = result.split('/*""*/')[0].strip()

            if not data:
                raise ValueError(
                    "Empty response from Google Search"
                )
        except Exception as e:
            raise ValueError(e)

        try:
            # find eof json
            jend = data.rfind("}")
            if jend >= 0:
                data = data[:jend + 1]
            # Attempt to load the JSON data
            jdata = orjson.loads(data)["d"]
            return orjson.loads(jdata[4:])

        except orjson.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON response: {e}")
            # self.logger.error(f"Raw response data: {data}")
            return None
        except Exception as e:
            self.logger.error(
                f"An error occurred during Google Search: {e}"
            )
            raise DriverError(
                f"Google Search Error: {e}"
            ) from e
