from typing import Any
from urllib.parse import urlencode
from asyncdb.exceptions import NoDataFound, ProviderError
from ...exceptions import DataNotFound, DriverError, QueryException
from ..sources import restSource


class uap(restSource):
    """UAP.

        Get all information from UAP API
    """
    base_url: str = 'https://{uap_url}/api/v1/{type}'
    type = 'adp_workers'
    _token = ''
    _page = 1
    _more_results: bool = True
    _page_size: int = 1000

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

        args = {}

        print('UAP CONDITIONS> ', conditions)
        if 'conditions' in self._conditions:
            self._conditions = self._conditions.pop('conditions')
        try:
            self._program = self._conditions['var']
            del self._conditions['var']
        except KeyError:
            self._program = None

        if 'program' in self._conditions:
            self._program = self._conditions['program']
            del self._conditions['program']

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            try:
                self.type = self.kwargs['method']
            except KeyError:
                pass

        if 'type' in self._conditions:
            self.type = self._conditions['type']
            del self._conditions['type']

        if 'method' in self._conditions:
            self.type = self._conditions['method']
            del self._conditions['method']

        # define type of function:
        args['type'] = self.type

        # authentication
        self._token = self._conditions.pop('token', None)
        if not self._token:
            self._token = self._env.get('UAP_TOKEN')
            if not self._token:
                try:
                    self._token = definition.params['token']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "UAP: Token missing"
                    ) from ex

        try:
            host = definition.params['host']
        except (KeyError, ValueError, AttributeError):
            host = 'uap.trocglobal.com'

        args['uap_url'] = self._env.get(
            'UAP_API',
            fallback=host
        )

        self._page_size = self._conditions.pop('pagesize', None)
        if not self._page_size:
            try:
                self._page_size = definition.params['pagesize']
            except (AttributeError, KeyError):
                self._page_size = 1000

        self._conditions['page_size'] = self._page_size

        self._more_results = self._conditions.pop('more_results', True)
        self._conditions['more_results'] = self._more_results
        # number of pages:
        self._pages = conditions.pop('pages', None)

        self._headers = {
            "Authorization": f"Token {self._token}",
            "Accept-Encoding": "gzip, deflate, br",
            "Content-Type": "application/json",
            "Host": host,
            **self._headers
        }

        try:
            del self._conditions['refresh']
            del self._conditions['ENV']
        except (KeyError, ValueError, TypeError):
            pass

        if self.type in ('adp-workers', 'adp_workers'):
            self.url = 'https://{uap_url}/api/v1/adp-workers'
        elif self.type == 'assets':
            self.method = 'get'
            self.url = 'https://{uap_url}/api/v1/employees/assets/'
        else:
            if not self._program:
                self.url = 'https://{uap_url}/api/v1/volt/{type}'
            else:
                self.url = 'https://{uap_url}/api/v1/volt/{type}/{program}'

        ## URL parameters
        self._args = args

    async def close(self):
        pass

    async def get_next_result(self, result):
        r = result['results']
        _next = True
        while _next is True:
            if self._pages:
                if self._page > self._pages:
                    # no more pages
                    break
            self._page = self._page + 1
            url = self.build_url(
                self.url, queryparams=urlencode(
                    {"page": self._page}
                )
            )
            print(f'Fetching page {self._page}')
            try:
                res, error = await self.request(url)
                if res:
                    pg = res['pagination']
                    r = r + res['results']
                    _next = bool(pg['next'])
                else:
                    print('Error Continue')
                    print(error)
                    continue
            except Exception as err:  # pylint: disable=W0703
                print('Error HERE')
                print(err)
                _next = False
        print(' ::  Returning Results :: ')
        return r

    async def adp_workers(self):
        self.type = 'adp-workers'
        self._args['type'] = self.type
        try:
            del self._conditions['attribute']
        except KeyError:
            pass
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(str(err))
            raise

    async def events(self):
        self.type = 'events'
        self._args['type'] = self.type
        self._args['program'] = self._program
        try:
            self._result = await self.query()
            return self._result
        except Exception as e:  # pylint: disable=W0703
            self.logger.exception(e)
        return False

    async def assets(self):
        self.type = 'assets'
        self._args['type'] = self.type
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            self.logger.exception(str(err))
            raise

    async def users(self):
        self.type = 'users'
        self._args['type'] = self.type
        self._args['program'] = self._program
        try:
            self._result = await self.query()
            return self._result
        except Exception as e:  # pylint: disable=W0703
            self.logger.exception(e)
        return False

    async def query(self, url: str = None, params: dict = None, data: dict = None):  # pylint: disable=W0237
        self._result = None
        if url:
            self._args['program'] = self._program
            self.url = self.build_url(
                url,
                queryparams=urlencode(params)
            )
        else:
            self._args['program'] = self._program
            # create URL
            self.url = self.build_url(
                self.url,
                args=self._args,
                queryparams=urlencode(self._conditions)
            )
        try:
            result, error = await self.request(
                self.url, self.method, data=data
            )
            if not result:
                raise DataNotFound(
                    f"UAP: No Data was found: {error}"
                )
            elif error:
                raise DriverError(str(error))
            else:
                try:
                    if result['errorCode']:
                        raise NoDataFound(
                            message=result['errorMessage'],
                            code=result['errorCode']
                        )
                except (TypeError, KeyError):
                    pass
            # processing data:
            if isinstance(result, list):
                return result
            pagination = {}
            try:
                pagination = result['pagination']
            except KeyError:
                pagination['next'] = False
            if self._more_results is True and bool(pagination['next']) is True:
                self._result = await self.get_next_result(result)
            else:
                try:
                    self._result = result['results']
                except (TypeError, KeyError) as ex:
                    raise DataNotFound(
                        f"Data not found: {ex}"
                    ) from ex
            return self._result
        except (DataNotFound, NoDataFound) as err:
            raise DataNotFound(
                str(err)
            ) from err
        except NoDataFound as err:
            print("UAP NO DATA FOUND: === ", err)
            raise DataNotFound(
                message=str(err)
            ) from err
        except (ProviderError, DriverError) as err:
            print("UAP PROVIDER ERROR: === ", err)
            raise DriverError(
                message=err.message, code=err.status_code
            ) from err
        except Exception as err:
            print("UAP ERROR: === ", err)
            self._result = None
            raise QueryException(
                message=str(err)
            ) from err
