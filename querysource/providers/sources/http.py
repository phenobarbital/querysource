import sys
from typing import (
    Any,
    Union,
    Optional
)
from collections.abc import Callable
from io import BytesIO
import asyncio
import random
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from functools import partial
# from traitlets import HasTraits
import urllib3
import requests
from requests.auth import HTTPBasicAuth
import httpx
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup as bs
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from lxml import html, etree
import xml.etree.ElementTree as ET
from asyncdb import AsyncDB
from asyncdb.utils import cPrint
from navconfig.logging import logging
from proxylists.proxies import ProxyWorld
from proxylists import check_address
from ...models import QueryModel
from ...utils.functions import check_empty
from ...exceptions import (
    DriverError,
    DataNotFound,
    QueryException
)
from ...conf import CACHE_URL
from .abstract import baseSource

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec


P = ParamSpec("P")


urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger(name='selenium.webdriver').setLevel(logging.WARNING)

xml_types = ['application/xhtml+xml', 'application/xml']

ua = [
    # Chrome - Desktop (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    # Chrome - Desktop (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",  # noqa
    # Safari - Desktop (Mac)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",  # noqa
    # Firefox - Desktop (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/118.0",
    # Edge - Desktop (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.2088.46",  # noqa
    # Chrome - Mobile (Android)
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36",  # noqa
    # Safari - Mobile (iOS)
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",  # noqa
    # Samsung Internet - Mobile (Android)
    "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/21.0 Chrome/118.0.0.0 Mobile Safari/537.36",  # noqa
    # Firefox - Mobile (Android)
    "Mozilla/5.0 (Android 13; Mobile; rv:118.0) Gecko/118.0 Firefox/118.0",
    # Opera - Desktop (Windows)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 OPR/104.0.0.0"  # noqa
    # Firefox - Desktop (Linux)
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Chrome - Desktop (Linux)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    # Other:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",  # noqa
]

mobile_ua = [
    "Mozilla/5.0 (Linux; Android 4.2.1; en-us; Nexus 5 Build/JOP40D) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19",  # noqa
    'Mozilla/5.0 (iPhone; CPU iPhone OS 12_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.0 Mobile/15E148 Safari/604.1',  # noqa
    'Mozilla/5.0 (Linux; Android 9; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.119 Mobile Safari/537.36',  # noqa
    'Mozilla/5.0 (Linux; Android 8.0.0; Pixel 2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.93 Mobile Safari/537.36',  # noqa
    'Mozilla/5.0 (Linux; Android 10; HUAWEI VOG-L29) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Mobile Safari/537.36',  # noqa
    'Mozilla/5.0 (iPad; CPU OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0 Mobile/15E148 Safari/604.1',  # noqa
]

class httpSource(baseSource):
    """httpSource.

    Origin of all HTTP-based Data Sources.
    """
    __parser__ = None
    url: str = None
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
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        'cache-control': 'max-age=0',
    }
    use_redis: bool = False

    def __init__(
        self,
        *args: P.args,
        slug: str = None,
        query: Any = None,
        qstype: str = '',  # migrate to Enum
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
        self.url: str = kwargs.pop('url', self.url)
        if not self.url:
            try:
                self.url = definition.source
            except AttributeError:
                pass
        if conditions and 'url' in conditions:
            self.url = conditions.pop('url')
        ### URL arguments:
        self._args: dict = {}
        ### Language:
        self.language = kwargs.pop('language', self.language)
        if not isinstance(self.language, list):
            self.language = [self.language]
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
        self._redis: Callable = None
        try:
            del kwargs['loop']
        except KeyError:
            pass
        self.use_proxies: bool = kwargs.pop('use_proxy', False)
        self._proxies: list = []
        self.rotate_ua: bool = kwargs.pop('rotate_ua', self.rotate_ua)
        ## User Agent Rotation:
        if self.rotate_ua is True:
            self._ua = random.choice(ua)
        else:
            self._ua: str = ua[0]
        ## Headers
        headers = kwargs.pop('headers', {})
        self._headers = {
            "Accept": self.accept,
            "Content-Type": self.content_type,
            "User-Agent": self._ua,
            **self.headers,
            **headers
        }
        ## referer information:
        self.referer = kwargs.pop('referer', None)
        if self.referer:
            self._headers['referer'] = self.referer
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
        self._user = kwargs.pop('user', '')
        if 'user' in self._conditions:
            self._user = self._conditions['user']
        self._pwd = kwargs.pop('password', '')
        if 'password' in self._conditions:
            self._pwd = self._conditions['password']
        ## BeautifulSoup Object:
        self._bs: Callable = None
        self._last_execution: dict = None
        # self.kwargs = kwargs
        # Check if use json as payload:
        self.use_json: bool = kwargs.pop('use_json', False)
        if self.use_redis is True:
            self._redis = AsyncDB('redis', dsn=CACHE_URL)
        # Calling the Post-Init Method:
        self.__post_init__(
            definition=self._definition,
            conditions=self._conditions,
            request=request,
            loop=loop,
            **kwargs
        )

    def __post_init__(
        self,
        definition: dict,
        conditions: dict,
        request: Any = None,
        **kwargs
    ) -> None:
        pass

    @property
    def html(self):
        return self._bs

    def last_execution(self):
        return self._last_execution

    async def get_proxies(self):
        p = []
        proxies = await ProxyWorld().get_list()
        for address in proxies:
            host, port = address.split(':')
            if await check_address(host=host, port=port) is True:
                p.append(f"http://{address}")
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

    async def http_request(
        self,
        url: str,
        method: str = 'get',
        data: dict = None,
        headers: Optional[dict] = None,
        use_json: bool = False,
        use_proxies: bool = False
    ):
        response = None
        error = None
        req_args = {
            "method": method.upper(),
            "url": url,
            "follow_redirects": True,
            "json" if use_json else "data": data
        }
        timeout = httpx.Timeout(self.timeout)
        if isinstance(headers, dict):
            headers = {**self._headers, **headers}
        else:
            headers = {**self._headers}
        args = {"timeout": timeout, "headers": headers}
        if use_proxies is True:
            pr = await self.get_proxies()
            proxy = random.choice(pr)
            # proxies = {"http://": proxy, "https://": proxy}
            # args['proxies'] = proxies
            args['proxy'] = proxy
        try:
            async with httpx.AsyncClient(**args) as client:
                response = await client.request(**req_args)
        except httpx.HTTPError as e:
            error = str(e)
        except Exception as exc:
            self.logger.error(str(exc))
            error = str(exc)
        return response, error

    async def async_request(
        self,
        url: str,
        method: str = "get",
        data: dict = None,
        cookies: dict = None,
        headers: dict = None,
        use_json: bool = False,
        namespaces: Optional[dict] = None,
        **kwargs
    ):
        """
        Asynchronously sends an HTTP request using HTTPx.

        :param url: The URL to send the request to.
        :param method: The HTTP method to use (e.g., 'GET', 'POST').
        :param data: The data to send in the request body.
        :param cookies: A dictionary of cookies to send with the request.
        :param headers: A dictionary of headers to send with the request.
        :param use_json: Whether to send the data as JSON.
        :return: A tuple containing the result and any error information.
        """
        result = []
        error = {}
        auth = None
        proxies = None
        if self.use_proxies is True:
            if self._proxies:
                proxy = random.choice(self._proxies)
                proxies = {"http": proxy, "https": proxy, "ftp": proxy}
        if isinstance(headers, dict):
            headers = {**self._headers, **headers}
        if self.auth:
            if "apikey" in self.auth:
                self.headers[
                    "Authorization"
                ] = f"{self.token_type} {self.auth['apikey']}"
            elif self.auth_type == "api_key":
                headers = {**self._headers, **self.auth}
            elif self.auth_type == 'key':
                url = self.build_url(
                    url,
                    args=self._arguments,
                    queryparams=urlencode(self.auth)
                )
            elif self.auth_type in ["basic", "auth", "user"]:
                auth = self.auth
        elif self._user and self.auth_type == "basic":
            auth = (self._user, self._pwd)
        cPrint(
            f"HTTP: Connecting to {url} using {method.upper()}", level="DEBUG"
        )
        timeout = httpx.Timeout(self.timeout)
        args = {"timeout": timeout, "headers": headers, "cookies": cookies}
        if auth is not None:
            args["auth"] = auth
        if proxies:
            args["proxies"] = proxies
        req_args = {
            "method": method.upper(),
            "url": url,
            "follow_redirects": True,
            "json" if use_json else "data": data
        }
        try:
            async with httpx.AsyncClient(**args) as client:
                response = await client.request(**req_args)
                result, error = await self.process_response(response, url, namespaces)
        except httpx.HTTPError as e:
            error = str(e)
        except Exception as exc:
            self.logger.error(str(exc))
            error = str(exc)
        if error:
            if isinstance(error, BaseException):
                raise error
            elif isinstance(error, bs):
                return (result, error)
            else:
                print('ERROR > ', error)
                raise DriverError(str(error))
        ## saving last execution parameters:
        self._last_execution = {
            "url": self.url,
            "method": method,
            "data": data,
            "auth": bool(auth),
            "proxies": proxies,
            "ua": self._ua,
            "headers": headers
        }
        return (result, error)

    async def evaluate_error(
        self,
        response: Union[str, list],
        message: Union[str, list, dict]
    ) -> tuple:
        """evaluate_response.

        Check Response status and available payloads.
        Args:
            response (_type_): _description_
            url (str): _description_

        Returns:
            tuple: _description_
        """
        if isinstance(response, list):
            # a list of potential errors:
            for msg in response:
                if message in msg:
                    return True
        if isinstance(response, dict) and "errors" in response:
            errors = response["errors"]
            if isinstance(errors, list):
                for error in errors:
                    try:
                        if message in error:
                            return True
                    except TypeError:
                        if message == error:
                            return True
            else:
                if message == errors:
                    return True
        else:
            if message in response:
                return True
        return False

    async def process_response(self, response, url: str, namespaces: Optional[dict] = None)  -> tuple:
        """
        Processes the response from an HTTPx request.

        :param response: The response object from httpx.
        :param url: The URL that was requested.
        : namespaces dict: XML Namespaces to be registered.
        :return: A tuple containing the processed result and any error information.
        """
        error = None
        result = None
        # Process the response
        status = self.response_status(response)

        if status >= 400:
            # Evaluate response body and headers.
            print(" == ERROR Headers == ")
            print(f"{response.headers}")
            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                message = response.json()
            elif "text/" in content_type:
                message = response.text
            elif "X-Error" in response.headers:
                message = response.headers["X-Error"]
            else:
                message = response.reason
            # Log the error or perform other error handling
            self.logger.error(
                f"Error: {message}, status: {status}"
            )
            if hasattr(self, 'no_errors'):
                for key, msg in self.no_errors.items():
                    if int(key) == status:
                        if await self.evaluate_error(message, msg):
                            return (response, status)
            # Raise an exception
            raise ConnectionError(
                f"HTTP Error {status}: {message}"
            )
        else:
            if self.accept == "application/json":
                try:
                    result = response.json()
                except Exception as e:
                    logging.error(e)
                    # is not an json, try first with beautiful soup:
                    try:
                        self._bs = bs(response.text, "html.parser")
                        result = self._bs
                    except Exception:
                        error = e
            elif getattr(self, 'download', None) is False:
                data = await response.aread()
                buffer = BytesIO(data)
                buffer.seek(0)
                result = buffer
            elif self.accept in ('text/html', 'application/xhtml+xml'):
                result = await response.aread()
                try:
                    # html parser for lxml
                    self._parser = html.fromstring(result)
                    # BeautifulSoup parser
                    self._bs = bs(response.text, "html.parser")
                    result = self._bs
                except Exception as e:
                    error = e
            elif any(mime in self.accept for mime in xml_types):
                result = await response.aread()
                try:
                    if namespaces:
                        for key, value in namespaces.items():
                            ET.register_namespace(key, value)
                    self._parser = etree.fromstring(result)
                except etree.XMLSyntaxError:
                    self._parser = html.fromstring(result)
                except Exception as e:
                    error = e
            else:
                # return the response as is as Text:
                result = await response.text
        return result, error

    @staticmethod
    def response_status(response):
        if hasattr(response, 'status_code'):
            return response.status_code

        return response.status

    async def selenium_request(
        self,
        url,
        method: str = 'get',
        data: Optional[dict] = None,
        headers: Optional[Union[dict, None]] = None
    ) -> Any:
        # Using Selenium to get information:
        user_agent = random.choice(ua)
        chrome_options = [
            "--headless",
            "--enable-automation",
            "--lang=en",
            "--disable-extensions",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-features=NetworkService",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            f"user-agent={user_agent}"
        ]
        options = Options()
        for option in chrome_options:
            options.add_argument(option)
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        self.logger.info(
            f'Downloading URL using Selenium: {url}'
        )
        page = None
        if method == 'get':
            driver.get(url)
            page = driver.page_source
        driver.quit()
        return page

    async def request(
        self,
        url,
        method: str = 'get',
        data: dict = None,
        cookies: dict = None,
        headers: Optional[Union[dict, None]] = None
    ):
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

        if headers is not None and isinstance(headers, dict):
            headers = {**self._headers, **headers}
        else:
            headers = self._headers.copy()
        if self.auth:
            if 'apikey' in self.auth:
                headers['Authorization'] = f"{self.token_type} {self.auth['apikey']}"
            elif self.auth_type == 'api_key':
                headers = {**self._headers, **self.auth}
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
                headers=headers,
                verify=False,
                auth=auth,
                params=data,
                timeout=self.timeout,
                proxies=proxies,
                cookies=cookies
            )
        elif method == 'post':
            if self.data_format == 'json':
                my_request = partial(
                    requests.post,
                    headers=headers,
                    json={"query": data},
                    verify=False,
                    auth=auth,
                    timeout=self.timeout,
                    proxies=proxies,
                    cookies=cookies
                )
            else:
                my_request = partial(
                    requests.post,
                    headers=headers,
                    data=data,
                    verify=False,
                    auth=auth,
                    timeout=self.timeout,
                    proxies=proxies
                )
        elif method == 'put':
            my_request = partial(
                requests.put,
                headers=headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        elif method == 'delete':
            my_request = partial(
                requests.delete,
                headers=headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        elif method == 'patch':
            my_request = partial(
                requests.patch,
                headers=headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies
            )
        else:
            my_request = partial(
                requests.post,
                headers=headers,
                data=data,
                verify=False,
                auth=auth,
                timeout=self.timeout,
                proxies=proxies,
                cookies=cookies
            )
        # making request
        loop = asyncio.get_event_loop()
        future = [
            loop.run_in_executor(executor, my_request, url)
        ]
        try:
            result, error = await self.process_request(future)
            if error:
                if isinstance(error, BaseException):
                    raise error
                elif isinstance(error, bs):
                    return (result, error)
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
                "headers": headers
            }
            return (result, error)
        except Exception as err:
            logging.exception(err)
            raise QueryException(f"Error: {err}") from err

    async def process_request(self, future):
        try:
            loop = asyncio.get_running_loop()
            asyncio.set_event_loop(loop)
            error = None
            for response in await asyncio.gather(*future):
                # getting the result, based on the Accept logic
                if self.accept in (
                    'application/xhtml+xml',
                    'text/html',
                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8"
                ):
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
                elif self.accept in ('text/plain', 'text/csv'):
                    result = response.text
                elif self.accept == 'application/json':
                    try:
                        result = self._encoder.loads(response.text)  # instead using .json method
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

    async def aquery(
        self,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        namespaces: Optional[dict] = None,
    ):
        """Run an async query on the Data Provider.
        """
        try:
            if self.use_proxies is True:
                self._proxies = await self.get_proxies()
        except AttributeError:
            pass
        # credentials calculation
        self.processing_credentials()
        # create URL
        self.url = self.build_url(
            self.url,
            args=self._args,
            queryparams=urlencode(self._conditions)
        )
        try:
            use_json = True if self.content_type == 'application/json' else self.use_json
            result, error = await self.async_request(
                self.url,
                method=self.method,
                data=data,
                use_json=use_json,
                headers=headers,
                namespaces=namespaces
            )
            if check_empty(result):
                raise DataNotFound(
                    message="No Data was found"
                )
            elif error:
                raise DriverError(
                    str(error)
                )
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

    async def query(self, data: dict = None):
        """Run a query on the Data Provider.
        """
        try:
            if self.use_proxies is True:
                self._proxies = await self.get_proxies()
        except AttributeError:
            pass
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
            if check_empty(result):
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
