"""
QuerySource Handler.

Class-based Views for creation of aiohttp API endpoints.
"""
from typing import (
    Optional
)
from aiohttp import web
from navigator.views import BaseHandler
from querysource.utils.parseqs import is_parseable


class QueryHandler(BaseHandler):

    def query_parameters(self, request: web.Request = None) -> dict:
        if not request:
            request = self.request
        return {key: val for (key, val) in request.query.items()}

    def parse_qs(self, request: web.Request = None) -> Optional[dict]:
        """get_queryparams.

        Accept strings formatted as list (with square brackets) or dict or tuples.
        values can be in the format:
        (1,2,3) or ("1","2","3") or [a,b,c] or {a,b,c}

        Args:
            request (web.Request, optional): Web Request.

        Returns:
            Optional[List]: parsed query strings as variables.
        """
        if not request:
            request = self.request

        try:
            qry = {}
            for key, val in request.rel_url.query.items():
                if parser:= is_parseable(val):
                    qry[key] = parser(val)
                else:
                    # is a norma string
                    qry[key] = val
        except (TypeError, ValueError):
            pass
        return qry
