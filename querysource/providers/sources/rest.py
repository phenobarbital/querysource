"""REST.

RestSource is the source for getting data from RESTful APIs.
"""
import sys
from typing import Any
import asyncio
import aiohttp
import orjson
from navconfig.logging import logging
from bs4 import BeautifulSoup as bs
import requests
import urllib3
from querysource.libs.json import json_decoder, json_encoder
from querysource.exceptions import DriverError
from .http import httpSource

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")

urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)


class restSource(httpSource):
    login_url: str = None
    auth_type: str = 'key'
    accept: str = 'application/json'
    content_type: str = 'application/json'
    method: str = 'post'
    _expiration: int = 1800

    def __init__(
            self,
            *args: P.args,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            loop: asyncio.AbstractEventLoop = None,
            **kwargs: P.kwargs
    ) -> None:
        super().__init__(
            *args,
            slug=None,
            qstype='REST',
            definition=definition,
            conditions=conditions,
            request=request,
            loop=loop,
            **kwargs
        )
        if not self._conditions: # pylint: disable=E0203
            self._conditions = {}
        self._conditions = {**self._conditions, **kwargs}
        self.__post_init__(
            definition=self._definition,
            conditions=self._conditions,
            request=request,
            **kwargs
        )
        if 'source' in self._conditions: # removing usused call to driver
            del self._conditions['source']

    def __post_init__(self, definition: dict, conditions: dict, request: Any = None, **kwargs) -> None:
        pass

    async def process_request(self, future):
        error = None
        try:
            for response in await asyncio.gather(*future):
                result = None
                try:
                    result = response.json()
                except (ValueError) as e:
                    error = e ### try using data from result
                    try:
                        result = self._encoder.loads(response.content.decode("utf-8", "replace"))
                    except orjson.JSONDecodeError as ex:
                        error = ex # is not a valid response
                        # self.logger.exception(ex)
                        # is not an json, try if is a html
                        try:
                            sp = bs(response.text, 'html.parser')
                            return ([], sp)
                        except Exception as err:
                            logging.exception(err)
                            raise DriverError(
                                f"REST: Cannot parse Response Data {err!r}", stacktrace=result
                            ) from ex
            return (result, error)
        except (requests.exceptions.ProxyError) as err:
            raise DriverError(
                f"Proxy Connection Error: {err!r}"
            ) from err
        except (requests.ReadTimeout) as ex:
            return ([], ex)
        except requests.exceptions.Timeout as err:
            return ([], err)
        except requests.exceptions.HTTPError as err:
            return ([], err)
        except (
            requests.exceptions.RequestException,
            ) as e:
            raise DriverError(
                f"HTTP Connection Error: {e!r}"
            ) from e
        except Exception as e:
            self.logger.exception(e)
            raise DriverError(
                f"HTTP Connection Error: {e!r}"
            ) from e


    async def jwt_token(self, user: str, password: str):
        result = None
        # get the redis connection
        try:
            await self._redis.connection()
        except Exception as err:
            logging.exception(f'REST error: {err!s}')
            raise
        # try if is saved on redis:
        try:
            result = await self._redis.get(self._saved_token)
            if result:
                data = json_decoder(result)
                token = data['token']
                logging.debug(':: REST: Using credentials in Cache')
                self.auth['apikey'] = token
                self._token = token
                return self._token
        except Exception as err:
            print(err)
            logging.exception(f'REST Redis Error: {err!s}')
            raise
        finally:
            await self._redis.close()
        data = {
            "password": password,
            "username": user,
        }
        result = None
        self._headers['Accept'] = 'application/json'
        # getting the authentication token
        # first: search in redis (with expiration)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.login_url,
                timeout=timeout,
                data=json_encoder(data),
                headers=self._headers
            ) as response:
                if response.status == 200:
                    try:
                        result = await response.json()
                        data = await response.text()
                        token = result['token']
                        # saving the token on redis with expiration:
                        self._token = token
                        self.auth['apikey'] = token
                        try:
                            status = await self._redis.setex(
                                self._saved_token,
                                data,
                                self._expiration
                            )
                            print('Token Saved?:', status)
                            return self._token
                        except Exception as err:
                            raise DriverError(
                                f"REST: Authentication Error: {err}"
                            ) from err
                        finally:
                            await self._redis.close()
                    except Exception as e:
                        print(e)
                        b = await response.content.read()
                        result = b.decode("utf-8")
                        raise DriverError(
                            f'Error: {result}'
                         ) from e
                else:
                    error = await response.text()
                    raise DriverError(
                        f'Error: {error}'
                    )
