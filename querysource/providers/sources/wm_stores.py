import sys
import asyncio
from typing import (
    Union,
    Any
)
import requests
from ...exceptions import DriverError
from ...models import QueryModel
from .http import httpSource

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")


class wm_stores(httpSource):

    url: str = 'https://www.walmart.com/store/finder?location={zipcode}&distance={distance}'
    distance: int = 50
    referer: str = 'https://www.walmart.com/'
    language: list = ['en-US']
    use_proxies: bool = True

    def __init__(
            self,
            *args: P.args,
            slug: str = None,
            query: Any = None,
            definition: Union[QueryModel, dict] = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs: P.kwargs
    ) -> None:
        try:
            self.zipcode: 0 = kwargs['zipcode']
            del kwargs['zipcode']
        except KeyError:
            self.zipcode: int = 0
        try:
            self.distance: int = kwargs['distance']
            del kwargs['distance']
        except KeyError:
            pass
        super().__init__(
            *args,
            slug=slug,
            query=query,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        ## adding parameters:
        self._args['zipcode'] = self.zipcode
        self._args['distance'] = self.distance
        self._headers['host'] = 'www.walmart.com'

    async def process_request(self, future):
        try:
            error = None
            for response in await asyncio.gather(*future):
                print('R ', response.text)
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
