from typing import Any
from .graphql import QueryError, graphqlSource


class graphcountries(graphqlSource):
    url: str = 'https://countries.trevorblades.com/'
    auth_type: str = None
    api_name: str = 'Countries'
    fields: str = """name
                native
                capital
                emoji
                currency
                phone
                languages {
                code
                name
                }
            """


    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        super(graphcountries, self).__post_init__(
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

        try:
            self.country = self._conditions['country']
        except KeyError:
            self.country = 'US'

        self._operation = 'country'
        self.operation = f'country(code: "{self.country}")'
        self._query = self.gql.format(operation=self.operation, fields=self.fields)


    async def info(self):
        try:
            self._result = await self.query(self._query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"{self.api_name}: {ex}"
            ) from ex
