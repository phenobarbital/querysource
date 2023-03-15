from typing import Any, Union
from lxml import html
from querysource.models import QueryModel
from querysource.exceptions import ParserError

class xpathParser:
    """Basic Parser for HTML content using Xpath.
    """
    model: dict = None

    def __init__(
            self,
            query: Any = None,
            definition: Union[QueryModel, dict] = None, # Model Object or a dictionary defining a Query.
            conditions: dict = None,
            **kwargs
    ) -> None:
        self.query = query
        self.definition = definition
        self.conditions = conditions
        self.kwargs = kwargs

    async def parse(self, response: Any) -> Union[list, dict]:
        try:
            result = {}
            parser = html.fromstring(response.text)
            for name, path in self.model.items():
                value = parser.xpath(path)
                if isinstance(value, list) and len(value) > 0:
                    value = value[0].strip()
                result[name] = value
            return result
        except ValueError as ex:
            raise ParserError(
                f"Error loading XPATH Parser data: {ex}"
            ) from ex
