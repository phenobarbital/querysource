"""
JSON Encoders.
"""
import sys
from datamodel.parsers.json import JSONContent

DefaultEncoder = JSONContent

if sys.version_info < (3, 10):
    from typing_extensions import ParamSpec
else:
    from typing import ParamSpec
P = ParamSpec("P")

class BaseEncoder:
    """
    Encoder replacement for json.dumps using orjson
    """

    def __init__(self, *args: P.args, **kwargs: P.kwargs):
        encoder = DefaultEncoder(*args, **kwargs)
        self.encode = encoder.__call__
