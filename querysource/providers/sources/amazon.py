### Sample From: https://github.com/scrapehero/amazon-seller-list/blob/master/amazon-seller-list.py
import sys
from typing import (
    Union,
    Any
)
from querysource.models import QueryModel
from .scrapper import scrapperSource
from .parsers.amproduct import amProduct

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")




class amazon(scrapperSource):
    """Scrapping an Amazon Product using xpath.
    """

    url: str = 'https://www.amazon.com/dp/{asin}/ref=olp-opf-redir?aod=1'
    referer: str = 'https://www.amazon.com/'
    language: list = ['en-US']

    def __init__(
            self,
            *args: P.args,
            definition: Union[QueryModel, dict] = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs: P.kwargs
    ) -> None:

        try:
            self.asin: str = kwargs['asin']
            del kwargs['asin']
        except KeyError:
            self.asin: str = 'B09655FJDB' # Sample ASIN
        ## Replace Parser with Amazon Product parser:
        self.__parser__ = amProduct
        super().__init__(
            *args,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        ## adding parameters:
        self._urlargs['asin'] = self.asin
