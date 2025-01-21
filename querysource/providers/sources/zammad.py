from typing import Any
from datetime import datetime
from urllib.parse import urlencode
import pytz
import urllib3
from navconfig.logging import logging
from asyncdb.exceptions import ProviderError, NoDataFound
from ..sources import restSource
from ...exceptions import DataNotFound

urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)

class zammad(restSource):
    """
        Zammad
        Get all tickets from Zammad API
    """
    method: str = 'GET'
    timeout: int = 60

    def __post_init__(
        self,
        definition: dict = None,
        conditions: dict = None,
        request: Any = None,
        **kwargs
    ) -> None:
        #print('_ARGS', self._args)
        #print('_COND', self._conditions)
        #print('DEF', definition)
        # Get API URL
        if 'api_url' in self._conditions:
            self._args['api_url'] = self._conditions['api_url']
            del self._conditions['api_url']
        else:
            self._args['api_url'] = self._env.get('ZAMMAD_API_URL')
            if not self._args['api_url']:
                try:
                    self._args['api_url'] = definition.params['api_url']
                except (ValueError, AttributeError) as err:
                    raise ValueError("Zammad: Missing API Env") from err
        self._args['api_url'] = self._env.get(self._args['api_url'], fallback=self._args['api_url'])
        self.base_url = '{api_url}/api/v1/tickets/?per_page=100&page={page}'
        self._args['page'] = 1

        # Get API Token
        if 'api_token' in self._conditions:
            api_token = self._conditions['api_token']
            del self._conditions['api_token']
        else:
            api_token = self._env.get('ZAMMAD_API_TOKEN')
        if not api_token:
            try:
                api_token = definition.params['api_token']
            except (ValueError, AttributeError) as err:
                raise ValueError("Zammad: Missing API Token") from err
        api_token = self._env.get(api_token, fallback=api_token)
        self._headers['Authorization'] = f'Bearer {api_token}'

    async def tickets(self):
        self._result = await self.query()
        return self._result

    async def query(self):
        self._result = []
        while 1:
            self.url = self.build_url(
                self.base_url,
                args=self._args
            )
            result, error = await self.request(
                        self.url,
                        self.method,
                        headers = self._headers
                    )
            if error is not None:
                    logging.error(f'Zammad Error: {error!s}')
            elif not result:
                return self._result
            else:
                if 'error' in result:
                    return [result]
                self._args['page'] += 1
                self._result += result
                #print('COUNT', len(self._result))
