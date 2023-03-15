from typing import Any
from datetime import datetime, timedelta
from urllib.parse import urlencode
from navconfig.logging import logging
from querysource.exceptions import DataNotFound
from .rest import restSource


class shoppertrack(restSource):
    """
      Shoppertrack.
        Getting Data from Shoppertrack.
    """

    base_url: str = 'https://stws.shoppertrak.com/EnterpriseFlash/v1.0/'
    _expiration: int = 3600
    auth_type: str = 'basic'

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
            self.type = 'traffic'

        if 'type' in conditions:
            self.type = conditions['type']
            del conditions['type']

        if 'normalized' in self._conditions:
            self._normalized = self._conditions['normalized']
            del self._conditions['normalized']
        else:
            self._normalized = False

        # Credentials
        if 'username' in self._conditions:
            self._user = self._conditions['username']
            del self._conditions['username']
        else:
            self._user = self._env.get('SHOPPERTRACK_USERNAME')
            if not self._user:
                try:
                    self._user = definition.params['username']
                except (ValueError, AttributeError) as ex:
                    raise ValueError("SHOPPERTRACK: Missing UserName") from ex

        if 'password' in self._conditions:
            self._pwd = self._conditions['password']
            del self._conditions['password']
        else:
            self._pwd = self._env.get('SHOPPERTRACK_PASSWORD')
            if not self._pwd:
                try:
                    self._pwd = definition.params['password']
                except (ValueError, AttributeError):
                    raise ValueError("SHOPPERTRACK: Missing Password")
        # get granularity and type
        try:
            conditions['granularity'] = definition.params['granularity']
        except (ValueError, AttributeError, KeyError):
            conditions['granularity'] = '15min'

        try:
            conditions['site_type'] = definition.params['site_type']
        except (ValueError, AttributeError, KeyError):
            conditions['site_type'] = 'all'

        if self.type == 'traffic':
            self.url = self.base_url + 'traffic/{granularity}/site/{site_type}'
        else:
            self.url = self.base_url + 'service/{granularity}/site/{site_type}'

        # start and end dates:
        try:
            try:
                start = self._conditions['start_time']
                del conditions['start_time']
            except KeyError:
                pass
            dt1 = datetime.strptime(self._conditions['start_time'], "%Y-%m-%d %H:%M")
            self._conditions['start_time'] = dt1.strftime("%Y%m%d%H%M")

            if not 'end_time' in self._conditions:
                dt2 = dt1 + timedelta(minutes=15)
            else:
                dt2 = datetime.strptime(self._conditions['end_time'], "%Y-%m-%d %H:%M")
            self._conditions['end_time'] = dt2.strftime("%Y%m%d%H%M")
        except Exception as err:
            raise Exception(f'ShopperTrack: Error with Date parameters {err}') from err

        self.headers['Content-Type'] = 'application/json'
        # details
        self._conditions['detail'] = 'store'
        # set parameters
        self._args = conditions

    async def all_traffic(self, granularity: str = '15min'):
        """all_traffic.

        Traffic for All Sites.
        """
        self._args['granularity'] = granularity
        self._args['site_type'] = 'all'
        self.method = 'GET'
        try:
            self._result = await self.query()
            return self._result
        except KeyError:
            raise Exception('Shoppertrack: Error in Data format, missing "sites" attribute.')
        except Exception as err:
            logging.exception(err)
            raise

    async def traffic(self, store: str, granularity: str = '15min'):
        """all_traffic.

        Traffic for All Sites.
        """
        self._args['granularity'] = granularity
        self._args['site_type'] = store
        self.method = 'GET'
        try:
            self._result = await self.query()
            return self._result
        except KeyError:
            raise Exception('Shoppertrack: Error in Data format, missing "sites" attribute.')
        except Exception as err:
            logging.exception(err)
            raise

    def normalized_data(self, result: list):
        if self._normalized:
            normalized = []
            for row in result['sites']:
                traffic = row['traffic'][0]
                res = {
                    "storeID": row['storeID'],
                    **traffic
                }
                normalized.append(res)
            return normalized
        else:
            return result['sites']


    async def query(self):
        """
            Query.
            Basic Query of Shoppertrack API.
        """
        self._result = None
        # initial connection
        await self.prepare_connection()
        # create URL
        self.url = self.build_url(
            self.url,
            args=self._args,
            queryparams=urlencode(self._conditions)
        )
        result = []
        try:
            result, error = await self.request(
                self.url, self.method
            )
            if 'error' in result:
                error = result['error']
                # TODO: capture all errors on API.
                if error['code'] == 'E101':
                    raise DataNotFound('Shoppertrack: Error on Start Time.')
            if error is not None:
                print(error)
                logging.error(f'Shoppertrack: Error: {error!s}')
            elif not result:
                raise DataNotFound('Shoppertrack: No data was found')
            else:
                self._result = self.normalized_data(result)
                return self._result
        except DataNotFound as err:
            raise
        except Exception as err:
            logging.error(f'Shoppertrack: Error: {err!s}')
