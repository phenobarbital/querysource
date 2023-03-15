import sys
from typing import (
    Any,
    Union
)
from collections.abc import Callable
import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from functools import partial
# from traitlets import HasTraits
import urllib3
import requests
from requests.auth import HTTPBasicAuth
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup as bs
from lxml import html, etree
from asyncdb import AsyncDB
from asyncdb.utils import cPrint
from navconfig.logging import logging
from proxylists.proxies import ProxyDB
from proxylists import check_address
from querysource.models import QueryModel
from querysource.exceptions import (
    DriverError,
    DataNotFound,
    QueryException
)
from settings.settings import CACHE_URL
from .abstract import baseSource

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")


urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)


ua = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.33",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/106.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/42.0.2311.135 Safari/537.36 Edge/12.10240",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_8_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:104.0) Gecko/20100101 Firefox/104.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:105.0) Gecko/20100101 Firefox/105.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:105.0) Gecko/20100101 Firefox/105.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 12_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
]

class httpSource(baseSource):
    """httpSource.

    Origin of all HTTP-based Data Sources.
    """
    __parser__ = None

    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"
    content_type: str = 'application/xhtml+xml'
    use_proxies: bool = False
    timeout: int = 60
    auth_type: str = 'key'
    token_type: str = 'Bearer'
    data_format: str = 'data'
    rotate_ua: bool = True
    language: list = ['en-GB', 'en-US']
    method: str = 'get'
    headers = {
        "Accept-Encoding":"gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        'cache-control':'max-age=0',
    }
    use_redis: bool = False

    def __init__(
            self,
            *args: P.args,
            slug: str = None,
            query: Any = None,
            qstype: str = '', # migrate to Enum
            definition: Union[QueryModel, dict] = None,
            conditions: dict = None,
            request: web.Request = None,
            loop: asyncio.AbstractEventLoop = None,
            **kwargs: P.kwargs
    ) -> None:
        """httpSource.

        Base class for all HTTP-based data sources.
        Args:
            slug (str, optional): _description_. Defaults to None.
            query (Any, optional): _description_. Defaults to None.
            qstype (str, optional): _description_. Defaults to ''.
            definition (Union[QueryModel, dict], optional): _description_. Defaults to None.
            conditions (dict, optional): _description_. Defaults to None.
            request (web.Request, optional): _description_. Defaults to None.
        """
        ## URL:
        try:
            self.url = kwargs['url']
            del kwargs['url']
        except KeyError:
            try:
                if definition.source:
                    self.url = definition.source
            except AttributeError:
                pass
        if conditions and 'url' in conditions:
            self.url = conditions['url']
            del conditions['url']
        if not hasattr(self, 'url'):
            self.url: str = None
        ### URL arguments:
        self._args: dict = {}
        ### Language:
        try:
            self.language = kwargs['language']
            del kwargs['language']
        except KeyError:
            pass
        super(httpSource, self).__init__(
            *args,
            slug=slug,
            qstype=qstype,
            query=query,
            definition=definition,
            conditions=conditions,
            request=request,
            loop=loop,
            **kwargs
        )
        self._redis = None
        try:
            del kwargs['loop']
        except KeyError:
            pass
        # self.logger.debug(f'URL :: {self.url}')
        try:
            self.use_proxies: bool = kwargs['use_proxy']
        except KeyError:
            pass
        self._proxies: list = []
        try:
            self.rotate_ua: bool = kwargs['rotate_ua']
        except KeyError:
            pass
        ## User Agent Rotation:
        if self.rotate_ua is True:
            self._ua = random.choice(ua)
        else:
            self._ua: str = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36'
        ## Headers
        try:
            headers = kwargs['headers']
        except KeyError:
            headers = {}
        self._headers = {
            "Accept": self.accept,
            "Content-Type": self.content_type,
            "User-Agent": self._ua,
            **self.headers,
            **headers
        }
        ## referer information:
        try:
            self.referer = kwargs['referer']
            del kwargs['referer']
            self._headers['referer'] = self.referer
        except KeyError:
            self.referer = None
        ### Language Header:
        langs = []
        for lang in self.language:
            lang_str = f"{lang};q=0.9"
            langs.append(lang_str)
        langs.append('ml;q=0.7')
        self._headers["Accept-Language"] = ','.join(langs)
        ## Auth Object:
        self.auth: dict = {}
        # authentication credentials
        if 'user' in kwargs:
            self._user = kwargs['user']
            del kwargs['user']
        elif 'user' in self._conditions:
            self._user = self._conditions['user']
        else:
            self._user = ''
        if 'password' in kwargs:
            self._pwd = kwargs['password']
            del kwargs['password']
        elif 'password' in self._conditions:
            self._pwd = self._conditions['password']
        else:
            self._pwd = ''
        ## BeautifulSoup Object:
        self._bs: Callable = None
        self._last_execution: dict = None
        # self.kwargs = kwargs
        if self.use_redis is True:
            self._redis = AsyncDB('redis', dsn=CACHE_URL)

    @property
    def html(self):
        return self._bs

    def last_execution(self):
        return self._last_execution

    async def get_proxies(self):
        p = []
        proxies = await ProxyDB().get_list()
        for address in proxies:
            host, port = address.split(':')
            if await check_address(host=host, port=port) is True:
                p.append(address)
        return p


    async def refresh_proxies(self):
        if self.use_proxies is True:
            self._proxies = await self.get_proxies()

    def processing_credentials(self):
        """Getting credentials (auth) from ENV variables.
        """
        for key, value in self.auth.items():
            try:
                default = getattr(self, value, self.auth[value])
            except KeyError:
                default = value
            val = self.get_env_value(value, default=default)
            self.auth[key] = val

    async def session(self, url: str = None, method: str = None, data: dict = None):
        """
        session.
        Connect to an http source using aiohttp.
        """
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        if not url:
            url = self.url
        if not method:
            method = self.method
        auth = {}
        proxy = None
        if self.auth:
            auth = self.auth
        if self._proxies:
            proxy = random.choice(self._proxies)
        async with aiohttp.ClientSession(auth) as session:
            if method == 'get':
                async with session.get(
                    url,
                    headers=self.headers,
                    timeout=timeout,
                    auth=auth,
                    proxy=proxy
                ) as response:
                    return response
            elif method == 'post':
                async with session.post(
                    url,
                    headers=self.headers,
                    timeout=timeout,
                    proxy=proxy,
                    auth=auth,
                    data=data
                ) as response:
                    return response

    async def request(self, url, method: str ='get', data: dict = None):
        """
        request
            connect to an http source
        """
        result = []
        error = {}
        auth = None
        executor = ThreadPoolExecutor(2)
        proxies = None
        if self.use_proxies is True:
            proxy = self._proxies.pop(0)
            proxies = {
              "http": proxy,
              "https": proxy,
              "ftp": proxy
            }
        if self.auth:
            if 'apikey' in self.auth:
                self._headers['Authorization'] = f"{self.token_type} {self.auth['apikey']}"
            elif self.auth_type == 'api_key':
                self._headers = {**self._headers, **self.auth}
            elif self.auth_type == 'key':
                url = self.build_url(
                    url,
                    args=self._arguments,
                    queryparams=urlencode(self.auth)
                )
            elif self.auth_type == 'basic':
                auth = HTTPBasicAuth(*self.auth)
            else:
                auth = HTTPBasicAuth(*self.auth)
        elif self._user:
            auth = HTTPBasicAuth(self._user, self._pwd)
        elif self.auth_type == 'basic':
            auth = HTTPBasicAuth(self._user, self._pwd)
        cPrint(f'HTTP: Connecting to {url} using {method}', level='DEBUG')
        if method == 'get':
            my_request = partial(
                requests.get,
                headers=self._headers,
                verify=False,
                auth=auth,
                params=data,
                timeout=self.timeout,
                proxies=proxies
            )
        elif method == 'post':
            if self.data_format == 'json':
                my_request = partial(
                    requests.post,
                    headers=self._headers,
                    json={"query": data},
                    verify=False,
                    auth=auth,
                    timeout=self.timeout,
                    proxies=proxies
                )
            else:
                my_request = partial(
                    requests.post,
                    headers=self._headers,
                    data=data,
                    verify=False,
                    auth=auth,
                    timeout=self.timeout,
                    proxies=proxies
                )
        elif method == 'put':
            my_request = partial(
                requests.put,
                headers=self._headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        elif method == 'delete':
            my_request = partial(
                requests.delete,
                headers=self._headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        elif method == 'patch':
            my_request = partial(
                requests.patch,
                headers=self._headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        else:
            my_request = partial(
                requests.post,
                headers=self._headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        # making request
        future = [
            self._loop.run_in_executor(executor, my_request, url)
        ]
        try:
            result, error = await self.process_request(future)
            if error:
                if isinstance(error, BaseException):
                    raise error
                else:
                    raise DriverError(str(error))
            ## saving last execution parameters:
            self._last_execution = {
                "url": self.url,
                "method": method,
                "data": data,
                "auth": bool(auth),
                "proxies": proxies,
                "ua": self._ua,
                "headers": self._headers
            }
            return (result, error)
        except Exception as err:
            logging.exception(err)
            raise QueryException(f"Error: {err}") from err

    async def process_request(self, future):
        try:
            error = None
            for response in await asyncio.gather(*future):
                # getting the result, based on the Accept logic
                if self.accept in ('application/xhtml+xml', 'text/html', "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"):
                    try:
                        # html parser for lxml
                        self._parser = html.fromstring(response.text)
                        # Returning a BeautifulSoup parser
                        self._bs = bs(response.text, 'html.parser')
                        result = self._bs
                    except (AttributeError, ValueError) as e:
                        error = e
                elif self.accept == 'application/xml':
                    try:
                        self._parser = etree.fromstring(response.text)
                    except (AttributeError, ValueError) as e:
                        error = e
                elif self.accept == 'application/json':
                    try:
                        result = self._encoder.loads(response.text) # instead using .json method
                        # result = response.json()
                    except (AttributeError, ValueError) as e:
                        logging.error(e)
                        # is not an json, try first with beautiful soup:
                        try:
                            self._bs = bs(response.text, 'html.parser')
                            result = self._bs
                        except (AttributeError, ValueError) as ex:
                            error = ex
                else:
                    try:
                        self._bs = bs(response.text, 'html.parser')
                    except (AttributeError, ValueError) as ex:
                        error = ex
                    result = response.text
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

    async def query(self, data: dict = None):
        """Run a query on the Data Provider.
        """
        if self.use_proxies is True:
            self._proxies = await self.get_proxies()
        # credentials calculation
        self.processing_credentials()
        # create URL
        self.url = self.build_url(
            self.url,
            args=self._args,
            queryparams=urlencode(self._conditions)
        )
        try:
            result, error = await self.request(
                self.url, self.method, data=data
            )
            if not result:
                raise DataNotFound(
                    message="No Data was found"
                )
            elif error:
                raise DriverError(str(error))
        except DataNotFound:
            raise
        except QueryException:
            raise
        except Exception as err:
            print(err)
            raise QueryException(
                f"Uncaught Error on HTTP: {err}"
            ) from err
        # if result then
        self._result = result
        return result
