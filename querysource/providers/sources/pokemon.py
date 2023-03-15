from typing import Any
from .graphql import QueryError, graphqlSource


class pokemon(graphqlSource):
    url: str = 'https://graphqlpokemon.favware.tech/v7'
    auth_type: str = None
    api_name: str = 'Pokemon'
    fields: str = """
        sprite
        num
        species
        color
        abilities {
            first {
              name
            }
            special {
              name
            }
        }
        weight
        height
    """


    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:
        super(pokemon, self).__post_init__(
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )

        try:
            self.pokemon = self._conditions['pokemon'].lower()
        except KeyError:
            self.pokemon = 'pikachu'

        self._operation = 'getPokemon'
        self.operation = f'getPokemon(pokemon: {self.pokemon})'
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
