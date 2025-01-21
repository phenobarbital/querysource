"""
SalesForce Provider.
"""
from simple_salesforce import Salesforce, SalesforceLogin
from simple_salesforce.exceptions import SalesforceMalformedRequest
from ..parsers.sosql import SOQLParser
from ..exceptions import DriverError, ParserError
from .external import externalProvider


class salesforceProvider(externalProvider):
    __parser__ = SOQLParser
    drvname = 'Salesforce'

    async def get_connection(self):
        self._session_id, self._instance = SalesforceLogin(
            **self._connection.get_parameters()
        )
        ## saving driver in another instance:
        self._driver = self._connection
        # at now, making a new connection:
        self._connection = Salesforce(
            **self._connection.get_parameters()
        )

    async def prepare_connection(self):
        if not self._connection:
            # TODO: get a new connection
            raise DriverError(
                f'{self.drvname}: connection is not prepared'
            )
        ### getting connection object:
        await self.get_connection()
        if self.is_raw is False:
            try:
                await self._parser.set_options()
                self._query = await self._parser.build_query(
                    self._connection
                )
                self._arguments = self._parser.filter
            except Exception as ex:
                raise ParserError(
                    f"{self.drvname}: Unable to parse Query: {ex}"
                ) from ex

    async def dry_run(self):
        """Running Build Query and return the Query to be executed (without execution).
        """
        try:
            self._query = await self._parser.build_query(
                self._connection
            )
        except Exception as ex:
            raise ParserError(
                f"{self.drvname} Unable to parse Query: {ex}"
            ) from ex
        return (self._query, None)

    async def _get_query(self):
        result = []
        error = None
        try:
            res = self._connection.query_all(self._query)
            try:
                result = res['records']
            except KeyError:
                error = 'Invalid Salesforce Resource, missing *records* object'
        except SalesforceMalformedRequest as ex:
            raise ParserError(
                f"SalesForce: Error parsing query: {ex}"
            ) from ex
        except Exception as ex:  # pylint: disable=W0718
            self._logger.exception(ex, stack_info=False)
            error = ex
        return result, error
