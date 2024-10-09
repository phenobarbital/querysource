from typing import Any
from urllib.parse import urlencode
from bs4 import BeautifulSoup as bs
from simple_salesforce import Salesforce
from io import StringIO
import pandas as pd
from ...exceptions import (
    DataNotFound,
    DriverError,
    QueryException
)
from .rest import restSource


class salesforce(restSource):
    """Salesforce.

        Get data with SOQL requests
    """
    api_version: str = 'v39.0'
    base_url: str = "{instance}/services/data/{api_version}/analytics"
    method: str = 'get'

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in kwargs:
            self.type = kwargs['type']
            del kwargs['type']

        elif 'attribute' in kwargs:
            self.type = kwargs['attribute']
            del kwargs['attribute']

        if 'type' in conditions:
            self.type = conditions['type']
            del conditions['type']

        # Instance
        if 'instance' in self._conditions:
            self.instance = self._conditions['instance']
            del self._conditions['instance']
        else:
            self.instance = self._env.get('SALESFORCE_INSTANCE')
            if not self.instance:
                try:
                    self.instance = definition.params['instance']
                except (ValueError, AttributeError) as ex:
                    raise ValueError("Salesforce: Missing Instance") from ex

        ## Version of API
        self.api_version = self._conditions.get('api_version', self.api_version)

        # Token
        if 'token' in self._conditions:
            self.token = self._conditions['token']
            del self._conditions['token']
        else:
            self.token = self._env.get('SALESFORCE_TOKEN')
            if not self.token:
                try:
                    self.token = definition.params['token']
                except (ValueError, AttributeError) as ex:
                    raise ValueError("Salesforce: Missing Token") from ex

        # Domain
        if 'domain' in self._conditions:
            self.domain = self._conditions['domain']
            del self._conditions['domain']
        else:
            self.domain = self._env.get('SALESFORCE_DOMAIN')
            if not self.domain:
                self.domain = None

        # Username
        if 'username' in self._conditions:
            self._user = self._conditions['username']
            del self._conditions['username']
        else:
            self._user = self._env.get('SALESFORCE_USERNAME')
            if not self._user:
                try:
                    self._user = definition.params['username']
                except (ValueError, AttributeError) as ex:
                    raise ValueError("Salesforce: Missing UserName") from ex

        # Password
        if 'password' in self._conditions:
            self._pwd = self._conditions['password']
            del self._conditions['password']
        else:
            self._pwd = self._env.get('SALESFORCE_PASSWORD')
            if not self._pwd:
                try:
                    self._pwd = definition.params['password']
                except (ValueError, AttributeError) as exc:
                    raise ValueError(
                        f"Salesforce: Missing Password: {exc}"
                    ) from exc

        # set parameters
        self._args = conditions

        # SalesForce instance
        self.sf = Salesforce(
            username=self._user,
            password=self._pwd,
            security_token=self.token,
            domain=self.domain
        )

        if self.type in ('report', 'report_metadata'):
            self.report_id = None
            if 'report_id' in self._conditions:
                self.report_id = self._conditions['report_id']
                del self._conditions['report_id']
            elif request is not None:
                ## Report information:
                if request.method == 'POST':
                    # comes from conditions:
                    self.report_id = self._conditions.get('report_id', None)
                elif request.method == 'GET':
                    # comes from URL
                    self.report_id = conditions.get('var', kwargs.get('report_id', None))
                else:
                    raise DriverError(
                        "Invalid HTTP Method for requesting a Report."
                    )
            if not self.report_id:
                raise DriverError(
                    "Missing Report ID."
                )

    async def report(self):
        """Report.

        Extract SalesForce Reports using the Report ID.
        """
        self._conditions = {}
        self._args = {}
        self.url = f"{self.instance}/{self.report_id}?isdtp=p1&export=1&enc=UTF-8&xf=csv"
        self.accept = 'text/csv'
        # self.accept = 'application/json'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def report_metadata(self):
        """Report.

        Extract SalesForce Reports using the Report ID.
        """
        self._conditions = {}
        self.url = self.base_url + "/reports/{report_id}/describe"
        self._args['instance'] = self.instance
        self._args['api_version'] = self.api_version
        self._args['report_id'] = self.report_id
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def report_list(self):
        """Report.

        Get a list of recently used SalesForce Reports.
        """
        self._conditions = {}
        self.url = self.base_url + "/reports"
        self._args['instance'] = self.instance
        self._args['api_version'] = self.api_version
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def dashboard_list(self):
        """Dashboard List.

        Get a list of recently used SalesForce Dashboards.
        """
        self._conditions = {}
        self.url = self.base_url + "/dashboards"
        self._args['instance'] = self.instance
        self._args['api_version'] = self.api_version
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(err)
            raise

    async def soql(self):
        res = self.sf.query_all(self._args['query'])
        self._result = res['records']
        return self._result

    async def all_fields(self):
        try:
            obj = self._args['object']
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"Salesforce: Missing Object Name: {exc}"
            ) from exc
        obj = getattr(self.sf, obj)
        desc = obj.describe()
        fields = []
        for f in desc['fields']:
            fields.append(f['name'])
        str_fields = ', '.join(fields)
        query = f'SELECT {str_fields} FROM {object}'
        res = self.sf.query_all(query)
        self._result = res['records']
        return self._result

    async def query(self, data: dict = None):
        if self.type == 'soql':
            return self.soql()
        if self.type == 'all_fields':
            return self.all()
        else:
            # credentials calculation
            self.processing_credentials()
            # create URL
            self.url = self.build_url(
                self.url,
                args=self._args,
                queryparams=urlencode(self._conditions)
            )
            args = {
                "cookies": {'sid': self.sf.session_id}
            }
            self._headers = self.sf.headers
            self.auth = None
            self._user = None
            try:
                result, error = await self.request(
                    self.url, self.method, data=data, **args
                )
                if self.type == 'report':
                    result = pd.read_csv(
                        StringIO(result),
                        sep=',',
                        low_memory=False
                    )
                if isinstance(error, bs):
                    raise DataNotFound(
                        str(error)
                    )
                elif isinstance(result, pd.DataFrame) and result.empty:
                    raise DataNotFound(
                        message="No Data was found"
                    )
                elif result is None:
                    raise DataNotFound(
                        message="No Data was found"
                    )
                elif error:
                    raise DriverError(str(error))
            except DataNotFound:
                raise
            except QueryException:
                raise
            except Exception as err:
                raise QueryException(
                    f"Uncaught Error on HTTP: {err}"
                ) from err
            # if result then
            self._result = result
            return result
