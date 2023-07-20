from typing import Any
from datetime import datetime
from urllib.parse import urlencode
import pytz
import urllib3
from navconfig.logging import logging
from asyncdb.exceptions import ProviderError, NoDataFound
from querysource.providers.sources import restSource


urllib3.disable_warnings()
logging.getLogger("urllib3").setLevel(logging.WARNING)

class mobileinsight(restSource):
    """
      Vision mobileinsight
        Get all information from Vision API
    """
    base_url: str = 'http://{api_url}/api/organizations/{orgid}/'
    login_url = 'https://{api_url}/api/authenticate'
    token_type: str = 'Bearer'
    _saved_token: str = 'navigator_mobileinsight_token'
    auth_type: str = 'api_key'
    auth: bool = True
    method: str = 'GET'
    timeout: int = 60
    use_redis: bool = True

    def __init__(
        self,
        definition: dict = None,
        conditions: dict = None,
        request: Any = None, **kwargs
    ):
        self._orgid = None
        self._formid = None
        super(mobileinsight, self).__init__(definition, conditions, request, **kwargs)

        args = self._args
        self._conditions = conditions

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in kwargs:
            self.type = kwargs['type']
            del kwargs['type']

        if 'type' in conditions:
            self.type = conditions['type']
            del conditions['type']

        # Authentication
        if 'user' in conditions:
            self._user = conditions['user']
            self._pwd = conditions['password']
            del conditions['user']
            del conditions['password']
        else:
            self._user = self._env.get('MOBILEINSIGHT_API_USER')
            self._pwd = self._env.get('MOBILEINSIGHT_API_PASSWORD')
            if self._user is None:
                try:
                    self._user = definition.params['user']
                    self._pwd = definition.params['password']
                except (ValueError, AttributeError) as ex:
                    raise ValueError(
                        "MobileInsight: Authentication missing"
                    ) from ex

        try:
            args['orgid'] = conditions['orgid']
            del conditions['orgid']
        except (ValueError, KeyError):
            try:
                args['orgid'] = definition.params['orgid']
            except (ValueError, AttributeError) as ex:
                raise ValueError(
                    "MobileInsight: No Organization ID defined"
                ) from ex

        # Organization ID:
        self._orgid = args['orgid']

        args['api_url'] = self._env.get(
            'MOBILEINSIGHT_API',
            fallback='api.mobileinsight.com'
        )
        self.login_url = self.login_url.format(**args)

        if self.type in ('activities', 'formdata'):
            try:
                tz = pytz.timezone('UTC')
                dt1 = datetime.strptime(conditions['startdate'], "%Y-%m-%dT%H:%M:%S")
                dt1 = tz.localize(dt1, is_dst=None)
                conditions['startdate'] = dt1.strftime("%Y-%m-%d %H:%M:%S%z")

                dt2 = datetime.strptime(conditions['enddate'], "%Y-%m-%dT%H:%M:%S")
                dt2 = tz.localize(dt2, is_dst=None)
                conditions['enddate'] = dt2.strftime("%Y-%m-%d %H:%M:%S%z")

                self._startdate = conditions['startdate']
                self._enddate = conditions['enddate']

                conditions['startDateTimeInMilli'] = int((dt1 - datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds()) * 1000
                conditions['endDateTimeInMilli'] = int((dt2 - datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds()) * 1000

            except Exception as err:
                logging.info(
                    f"mobileinsight API: wrong date format: {err}"
                )
                raise ProviderError(
                    f"MobileInsight API: wrong date format: {err}"
                ) from err

            # remove unused conditions
            try:
                del conditions['startdate']
                del conditions['enddate']
                del conditions['refresh']
                del conditions['ENV']
                del conditions['dwh']
            except (KeyError, ValueError, TypeError):
                pass

            if self.type == 'formdata':
                try:
                    self._args['formid'] = conditions['formid']
                    del conditions['formid']
                except (ValueError, KeyError):
                    try:
                        args['formid'] = definition.params['formid']
                    except (ValueError, KeyError) as ex:
                        raise KeyError(
                            "MobileInsight: No form ID passed, form Id needed"
                        ) from ex
                self._formid = args['formid']
                self.url = 'http://{api_url}/api/organizations/{orgid}/views/formData/{formid}'

            elif self.type == 'activities':
                self.url = 'http://{api_url}/api/organizations/{orgid}/views/activities'
            else:
                logging.error(f'Wrong MI API {self.type}')
        # other conditions
        elif self.type == 'activities_list':
            self.url = 'http://{api_url}/api/organizations/{orgid}/activities'
        elif self.type == 'forms':
            self.url = 'http://{api_url}/api/organizations/{orgid}/forms/allForms'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
        elif self.type == 'photo_categories':
            self.url = 'https://{api_url}/api/organizations/{orgid}/photos/categories'
        elif self.type == 'metadata':
            try:
                args['formid'] = conditions['formid']
                del conditions['formid']
            except (ValueError, KeyError):
                try:
                    args['formid'] = definition.params['formid']
                except (ValueError, AttributeError) as ex:
                    raise KeyError(
                        "MobileInsight: No form ID passed, form Id needed"
                    ) from ex
            self._formid = args['formid']

            self.url = 'http://{api_url}/api/organizations/{orgid}/views/formMetaData/{formid}'
        elif self.type == 'store_types':
            self.url = 'http://{api_url}/api/organizations/{orgid}/storeTypes'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'stores':
            self.timeout = 20
            self.url = 'http://{api_url}/api/organizations/{orgid}/stores'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            if 'pageSize' not in conditions:
                conditions["pageSize"] = 100
        elif self.type == 'store_designations':
            self.url = 'http://{api_url}/api/organizations/{orgid}/storeDesignations'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, AttributeError):
                pass
            conditions = {}
        elif self.type == 'geographies':
            self.url = 'http://{api_url}/api/organizations/{orgid}/geographies'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'account_names':
            self.url = 'http://{api_url}/api/organizations/{orgid}/accounts'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'clients':
            self.url = 'http://{api_url}/api/organizations/{orgid}/clients'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'projects':
            self.url = 'http://{api_url}/api/organizations/{orgid}/projects'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'endpoints':
            self.url = 'http://{api_url}/api/organizations/{orgid}/endpoints'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'users':
            self.url = 'http://{api_url}/api/organizations/{orgid}/users'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'users_details':
            self.url = 'http://{api_url}/api/organizations/{orgid}/users/vocinityUserDetails'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'roles':
            self.url = 'http://{api_url}/api/organizations/{orgid}/roles'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
            conditions = {}
        elif self.type == 'storeprojects':
            self.url = 'http://{api_url}/api/organizations/{orgid}/projects/storeToProjects/{store_id}'
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
        elif self.type == 'photos':
            self.timeout = 120
            self.url = 'http://{api_url}/api/organizations/{orgid}/photoViewItems'
            if 'startdate' in conditions:
                conditions['visitDateFrom'] = conditions['startdate']
                del conditions['startdate']
            if 'enddate' in conditions:
                conditions['visitDateTo'] = conditions['enddate']
                del conditions['enddate']
            try:
                del conditions['refresh']
            except (KeyError, ValueError, TypeError):
                pass
        ## set args:
        self._args = args

    async def formdata(self):
        self.url = 'http://{api_url}/api/organizations/{orgid}/views/formData/{formid}'
        self.type = 'formdata'
        ## ID of Form
        try:
            self._args['formid'] = self._conditions['formid']
            del self._conditions['formid']
        except (ValueError, KeyError) as ex:
            raise KeyError(
                "MobileInsight: No form ID passed, form Id needed"
            ) from ex
        self._formid = self._args['formid']
        ### start and end
        try:
            tz = pytz.timezone('UTC')
            dt1 = datetime.strptime(self._conditions['startdate'], "%Y-%m-%dT%H:%M:%S")
            dt1 = tz.localize(dt1, is_dst=None)
            self._conditions['startdate'] = dt1.strftime("%Y-%m-%d %H:%M:%S%z")

            dt2 = datetime.strptime(self._conditions['enddate'], "%Y-%m-%dT%H:%M:%S")
            dt2 = tz.localize(dt2, is_dst=None)
            self._conditions['enddate'] = dt2.strftime("%Y-%m-%d %H:%M:%S%z")

            self._startdate = self._conditions['startdate']
            self._enddate = self._conditions['enddate']

            self._conditions['startDateTimeInMilli'] = int((dt1 - datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds()) * 1000
            self._conditions['endDateTimeInMilli'] = int((dt2 - datetime(1970, 1, 1, tzinfo=pytz.utc)).total_seconds()) * 1000

        except Exception as err:
            logging.error(
                f"mobileinsight API: wrong date format: {err}"
            )
            raise ValueError(
                f"mobileinsight: Wrong Date Format: {err}"
            ) from err

        del self._conditions['startdate']
        del self._conditions['enddate']
        try:
            del self._conditions['refresh']
        except (KeyError, ValueError, TypeError):
            pass
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def photos(self):
        self.url = 'http://{api_url}/api/organizations/{orgid}/photoViewItems'
        try:
            if 'startdate' in self._conditions:
                self._conditions['visitDateFrom'] = self._conditions['startdate']
                del self._conditions['startdate']
            if 'enddate' in self._conditions:
                self._conditions['visitDateTo'] = self._conditions['enddate']
                del self._conditions['enddate']
        except (KeyError, ValueError, TypeError) as ex:
            raise ValueError(
                f"MobileInsight Photos: Missing Parameters: {ex}"
            ) from ex
        try:
            del self._conditions['refresh']
        except (KeyError, ValueError, TypeError):
            pass
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def storeprojects(self):
        self.url = 'http://{api_url}/api/organizations/{orgid}/projects/storeToProjects/{store_id}'
        try:
            del self._conditions['refresh']
        except (KeyError, ValueError, TypeError):
            pass
        try:
            # if not self._args['store_id']:
            self._args['store_id'] = self._conditions['store_id']
            del self._conditions['store_id']
        except (KeyError, AttributeError) as ex:
            raise ValueError(
                "MobileInsight: Missing Store ID"
            ) from ex
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def userprojects(self):
        self.url = 'http://{api_url}/api/organizations/{orgid}/projects/userToProjects/{user_id}'
        try:
            del self._conditions['refresh']
        except (KeyError, ValueError, TypeError):
            pass
        try:
            self._args['user_id'] = self._conditions['user_id']
            del self._conditions['user_id']
        except (KeyError, AttributeError) as ex:
            raise ValueError(
                "MobileInsight: Missing User ID"
            ) from ex
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def get_next_result(self, result):
        # r = result['list'] # discarding previous data:
        r = []
        next = True
        page = 0
        num = 0
        while next == True:
            url = self.build_url(self.url, queryparams=urlencode({"pageNumber": page}))
            print('Fetching page %s' % page)
            page = page + 1
            try:
                res, error = await self.request(url)
                if error:
                    print(error)
                    next = False
                    break
                data = res['list']
                num += len(data) + 1
                if len(data) > 0:
                    r.extend(data)
                else:
                    next = False
                    break
            except Exception as err:
                print('Error HERE')
                print(err)
                next = False
        print('::  Returning Results')
        # return OrderedDict((frozenset(item.items()),item) for item in r).values()
        return [i for n, i in enumerate(r) if i not in r[n + 1:]]
        # return r

    async def query(self, url: str = None, params: dict = {}):
        g = {}
        if url:
            self.url = self.build_url(url, queryparams=urlencode(params))
        else:
            # create URL
            self.url = self.build_url(
                self.url,
                args=self._args,
                queryparams=urlencode(self._conditions)
            )
            print(self.url)
        ## getting auth token:
        token = await self.jwt_token(user=self._user, password=self._pwd)
        try:
            result, error = await self.request(
                self.url, self.method
            )
            if not result:
                raise NoDataFound(f"No Data was found: {error}")
            elif error:
                raise ProviderError(str(error))
            else:
                try:
                    if result['errorCode']:
                        raise NoDataFound(result['errorMessage'], code=result['errorCode'])
                except (TypeError, KeyError):
                    pass
                if self.type == 'metadata' or self.type == 'activities_list':
                    self._result = result
                elif self.type == 'forms' or self.type == 'activities' or self.type == 'users_details' or self.type == 'roles':
                    data = []
                    for form in result:
                        form['orgid'] = self._orgid
                        data.append(form)
                    self._result = data
                elif self.type == 'account_names':
                    data = result['list']
                    for form in data:
                        form['orgid'] = self._orgid
                    self._result = data
                elif self.type == 'stores' or self.type =='users' or self.type == 'endpoints' or self.type == 'photos':
                    # data = result['list']
                    self._result = await self.get_next_result(result)
                elif self.type == 'formdata':
                    data = []
                    for formId, res in enumerate(result):
                        frmId = None
                        c = (formId + 1)
                        el = []
                        for form in res:
                            if form['columnName'] == 'FormVisitID':
                                frmId = form['data']
                            el.append({
                                'formId': c,
                                'columnName': form['columnName'],
                                'data': form['data'],
                                'startDate': self._startdate,
                                'endDate': self._enddate,
                                'orgid': self._orgid,
                                'formid': self._formid
                            })
                        # update the key:
                        for d in el:
                            d.update((k, frmId) for k, v in d.items() if k == 'formId' and v == c)
                        data = data + el
                    self._result = data
                else:
                    self._result = result
                    #raise NotSupported("mobileinsight Method not Supported", code=503)
        except NoDataFound as err:
            print("MOBILEINSIGHT NO DATA FOUND: === ", err)
            #raise NoDataFound(err)
            self._result = None
        except ProviderError as err:
            print("MOBILEINSIGHT PROVIDER ERROR: === ", err)
            raise ProviderError(err.message, code=err.status_code)
        except Exception as err:
            print("MOBILEINSIGHT ERROR: === ", err)
            self._result = None
            raise ProviderError(str(result))
        finally:
            return self._result
