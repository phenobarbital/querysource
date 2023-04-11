from typing import Any
from querysource.exceptions import (
    DriverError,
    QueryError,
    DataNotFound,
    ConfigError
)
from querysource.providers.sources import restSource

gql = '''
{{
   actor {{
      account(id: {account_id}) {{
         nrql(query: "{sql}") {{
            results
         }}
      }}
   }}
}}
'''


class newrelic(restSource):
    """
      New Relic NQL
        API for get events from NewRelic
    """
    url: str = 'https://api.newrelic.com/graphql'
    nrql = "SELECT {fields} FROM {object} {options}"
    auth_type: str = 'api_key'
    method: str = 'post' # All calls will be POST
    data_format: str = 'json'
    accept: str = "*/*"

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        args = {}
        print('NewRelic CONDITIONS> ', conditions)
        self._query: dict = {}
        self._range:str = '7 DAYS'
        self._limit: str = 'MAX'
        self.auth: dict = {
            "API-Key": None
        }
        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in self._conditions:
            self.type = self._conditions['type']
            del self._conditions['type']

        if 'range' in self._conditions:
            self._range = self._conditions['range']
            del self._conditions['range']

        if 'limit' in self._conditions:
            self._limit = self._conditions['limit']
            del self._conditions['limit']

        if 'account_id' not in self._conditions:
            self._conditions['account_id'] = self._env.get('NEW_RELIC_ACCOUNT')
            if not self._conditions['account_id']:
                try:
                    self._conditions['account_id'] = definition.params['account_id']
                except (ValueError, AttributeError) as ex:
                    raise ConfigError(
                        "NewRelic: Missing Account ID"
                    ) from ex


        if 'api_key' in self._conditions:
            self.auth['API-Key'] = self._conditions['api_key']
            del self._conditions['api_key']
        else:
            self.auth['API-Key'] = self._env.get('NEW_RELIC_API_KEY')
            if not self.auth['API-Key']:
                try:
                    self.auth['API-Key'] = definition.params['api_key']
                except (ValueError, AttributeError) as ex:
                    raise ConfigError(
                        "NewRelic: Missing API Key"
                    ) from ex

        # can build the Query
        try:
            self._fields = self._conditions['fields']
            del self._conditions['fields']
        except KeyError:
            self._fields = '*'
        # event name
        try:
            self._event = self._conditions['event']
            del self._conditions['event']
        except KeyError:
            self._event = 'Agents'

        ## URL parameters
        self._args = args

        if self.type == 'eventlist':
            self._query = gql.format(
                account_id=self._conditions['account_id'],
                sql="SHOW EVENT TYPES SINCE 30 days AGO"
            )
        elif self.type == 'events':
            try:
                sql = self.nrql.format(
                    fields=self._fields,
                    object=self._event,
                    options=f'SINCE {self._range} AGO LIMIT {self._limit}'
                )
            except Exception as err:
                print(err)
                raise
            self._query = gql.format(
                account_id=self._conditions['account_id'],
                sql=sql
            )
        elif self.type == 'agents':
            sql = self.nrql.format(
                fields=self._fields,
                object='Agents',
                options=f'SINCE {self._range} AGO LIMIT {self._limit}'
            )
            self._query = gql.format(
                account_id=self._conditions['account_id'],
                sql=sql
            )
        elif self.type == 'sip':
            sql = self.nrql.format(
                fields=self._fields,
                object='Sip',
                options=f'SINCE {self._range} AGO TIMESERIES'
            )
            self._query = gql.format(
                account_id=self._conditions['account_id'],
                sql=sql
            )

    async def eventlist(self):
        try:
            query = gql.format(
                account_id=self._conditions['account_id'],
                sql="SHOW EVENT TYPES SINCE 30 days AGO"
            )
        except KeyError as err:
            self.logger.error(f'NewRelic: Missing key: {err}')
        try:
            self._result = await self.query(query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"NewRelic: {ex}"
            ) from ex

    async def events(self):
        try:
            sql = self.nrql.format(
                fields=self._fields,
                object=self._event,
                options=f'SINCE {self._range} AGO LIMIT {self._limit}'
            )
        except Exception as err:
            print(err)
            raise
        query = gql.format(
            account_id=self._conditions['account_id'],
            sql=sql
        )
        try:
            self._result = await self.query(query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"NewRelic: {ex}"
            ) from ex

    async def sip(self):
        sql = self.nrql.format(
            fields=self._fields,
            object='Sip',
            options=f'SINCE {self._range} AGO TIMESERIES'
        )
        query = gql.format(
            account_id=self._conditions['account_id'],
            sql=sql
        )
        try:
            self._result = await self.query(query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"NewRelic: {ex}"
            ) from ex

    async def agents(self):
        sql = self.nrql.format(
            fields=self._fields,
            object='Agents',
            options=f'SINCE {self._range} AGO LIMIT {self._limit}'
        )
        query = gql.format(
            account_id=self._conditions['account_id'],
            sql=sql
        )
        try:
            self._result = await self.query(query)
            return self._result
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"NewRelic: {ex}"
            ) from ex

    async def query(self, data: str = None):
        result = None
        if not data:
            data = self._query
        if not data:
            raise DriverError(
                "New Relic: GraphQL query is missing"
            )
        try:
            result, error = await self.request(
                self.url,
                self.method,
                data=data
            )
            if not result:
                raise DataNotFound(
                    f"NewRelic: No Data was found: {error}".format(error)
                )
            elif error:
                raise DriverError(str(error))
            elif 'errors' in result:
                raise DriverError(result['errors'][0]['message'])
        except DataNotFound:
            raise
        except Exception as ex:
            self.logger.exception(ex)
            raise QueryError(
                f"NewRelic: {ex}"
            ) from ex
        # if result then
        try:
            # {'data': {'actor': {'account': {'nrql': {'results'
            result = result['data']['actor']['account']['nrql']['results']
        except (ValueError, KeyError) as ex:
            raise QueryError(
                f'NewRelic: Incorrect Data result format: {ex}'
            ) from ex
        self._result = result
        return result
