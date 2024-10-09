from typing import Any
from .graphql import ConfigError, QueryError, graphqlSource


class swop(graphqlSource):
    url: str = 'https://swop.cx/graphql'
    token_type: str = 'ApiKey'
    env_key: str = 'SWOP_API'
    api_name: str = 'SWOP'
    base_currency: str = 'USD'
    quote_currencies: list = ["EUR"]

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        super(swop, self).__post_init__(
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        try:
            self.amount = self._conditions['amount']
        except KeyError:
            self.amount = None

        try:
            self.base_currency = self._conditions['currency']
        except KeyError as ex:
            raise ConfigError(
                "Missing Base Currency"
            ) from ex

        try:
            self.quote_currencies = self.encode_string(self._conditions['quote_currencies'])
        except KeyError as ex:
            raise ConfigError(
                "Missing Base Currency"
            ) from ex

        if self.type == 'latest':
            self._operation = 'latest'
            self.operation = f'latest(baseCurrency: "{self.base_currency}", quoteCurrencies: {self.quote_currencies!s})'
            self.fields = """date
                baseCurrency
                quoteCurrency
                quote
            """
            self._query = self.gql.format(operation=self.operation, fields=self.fields)

    async def latest(self):
        try:
            self.operation = f'latest(baseCurrency: "{self.base_currency}", quoteCurrencies: {self.quote_currencies!s})'
            self.fields = """
                date
                baseCurrency
                quoteCurrency
                quote
            """
            query = self.gql.format(operation=self.operation, fields=self.fields)
            self._operation = 'latest'
        except KeyError as err:
            self.logger.error(f'{self.api_name}: Missing key: {err}')
        try:
            self._result = await self.query(query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"{self.api_name}: {ex}"
            ) from ex

    async def convert(self):
        try:
            self.operation = f'convert(amount: {self.amount}, baseCurrency: "{self.base_currency}", quoteCurrencies: {self.quote_currencies!s})'
            self.fields = """
                date
                baseCurrency
                quoteCurrency
                baseAmount
                quoteAmount
            """
            query = self.gql.format(operation=self.operation, fields=self.fields)
            print('Q ', query)
            self._operation = 'convert'
        except KeyError as err:
            self.logger.error(f'{self.api_name}: Missing key: {err}')
        try:
            self._result = await self.query(query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"{self.api_name}: {ex}"
            ) from ex
