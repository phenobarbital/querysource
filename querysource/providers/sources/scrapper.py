### Sample From: https://github.com/scrapehero/amazon-seller-list/blob/master/amazon-seller-list.py
"""Scrapper.

Item Scrapper using a simple XPATH Parser.
"""
import sys
import asyncio
from typing import (
    Union,
    Any
)
import requests
from querysource.exceptions import DriverError
from querysource.models import QueryModel
from .http import httpSource
from .parsers.xpath import xpathParser

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")


class scrapperSource(httpSource):

    url: str = None
    referer: str = None
    language: list = ['en-US']
    __parser__ = xpathParser

    def __init__(
            self,
            *args: P.args,
            definition: Union[QueryModel, dict] = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs: P.kwargs
    ) -> None:
        super().__init__(
            *args,
            slug=None,
            query=self.url,
            qstype='HTTP Scrapper',
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )


    async def process_request(self, future):
        try:
            error = None
            for response in await asyncio.gather(*future):
                result = await self._parser.parse(response)
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
