import logging
import requests
import rapidjson
import aiohttp
import json
from .rest import restSource
from querysource.exceptions import *


class icims(restSource):
    """
      ICIMS
        Getting Data from ICIMS servers.
    """

    url: str = 'https://api.icims.com/'
    original_url: str = 'https://api.icims.com/' # Keep original url 
    login_url = 'https://login.icims.com/oauth/token'
    stream_url: str = 'https://data-transfer-assembler.production.env.icims.tools/datastream/v2/streams/{customer_id}/'
    _token: str = None
    _expiration: int = 1800
    _token_type: str = 'Bearer'
    _caching: bool = True
    _saved_token: str = 'navigator_icims_token'
    _legacy_call: bool = False
    _is_test: bool = False

    def __init__(self, definition=None, params: dict = {}, **kwargs):
        super(icims, self).__init__(definition, params, **kwargs)

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None

        if 'type' in params:
            self.type = params['type']
            del params['type']

        # Credentials
        if 'api_key' in self._params:
            self.client_id = self._params['api_key']
            del self._params['api_key']
        else:
            self.client_id = self._env.get('ICIMS_API_KEY')
            if not self.client_id:
                try:
                    self.client_id = definition.params['api_key']
                except (ValueError, AttributeError):
                    raise ValueError("ICIMS: Missing API Key")

        if 'api_secret' in self._params:
            self.client_secret = self._params['api_secret']
            del self._params['api_secret']
        else:
            self.client_secret = self._env.get('ICIMS_API_SECRET')
            if not self.client_secret:
                try:
                    self.client_secret = definition.params['api_secret']
                except (ValueError, AttributeError):
                    raise ValueError("ICIMS: Missing API Secret")

        if 'api_username' in self._params:
            self.api_username = self._params['api_username']
            del self._params['api_username']
        else:
            self.api_username = self._env.get('ICIMS_API_USERNAME')
            if not self.api_username:
                try:
                    self.api_username = definition.params['api_username']
                except (ValueError, AttributeError):
                    raise ValueError("ICIMS: Missing API Username")

        if 'api_password' in self._params:
            self.api_password = self._params['api_password']
            del self._params['api_password']
        else:
            self.api_password = self._env.get('ICIMS_API_PASSWORD')
            if not self.api_password:
                try:
                    self.api_password = definition.params['api_password']
                except (ValueError, AttributeError):
                    raise ValueError("ICIMS: Missing API Password")

        self.original_url = self.url
        
        # check if the call is legacy
        if 'legacy' in self._params and self._params['legacy'] is True:
            self.setup_legacy_request()
            del self._params['legacy']

        # check if the request is a test (To only get limited records)
        if 'test' in self._params and self._params['test'] is True:
            self._is_test = True
            del self._params['test']

        # if types
        # if self.type == 'people':
        #     self.url = self.url + 'customers/{customer_id}/search/people'
        # elif self.type == 'person':
        #     self.url = self.url + 'customers/{customer_id}/people/{person_id}'

        # set parameters
        self._args = params


    async def get_token(self):
        result = None
        # get the redis connection
        try:
            await self._redis.connection()
        except Exception as err:
            logging.exception(f'REST ICIM error: {err!s}')
            raise
        # try if is saved on redis:
        try:
            result = await self._redis.get(self._saved_token)
            if result:
                data = rapidjson.loads(result)
                logging.debug(':: ICIMS: Using credentials in Cache')
                self._token = data['access_token']
                self._token_type = data['token_type']
                return self._token
        except Exception as err:
            print(err)
            logging.exception(f'ICIMS Redis Error: {err!s}')
            raise
        # else: get token from URL
        data = {
            "audience": 'https://api.icims.com/v1/',
            "grant_type": 'client_credentials',
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        result = None
        # getting the authentication token
        # first: search in redis (with expiration)
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.login_url,
                timeout=timeout,
                data=data
            ) as response:
                if response.status == 200:
                    try:
                        result = await response.json()
                        data = await response.text()
                        # saving the token on redis with expiration:
                        self._token = result['access_token']
                        self._expiration = result['expires_in']
                        self._token_type = result['token_type']
                        try:
                            status = await self._redis.setex(
                                self._saved_token,
                                data,
                                self._expiration
                            )
                            print(status)
                        except Exception as err:
                            print(err)
                        finally:
                            await self._redis.close()
                            return self._token
                    except Exception as e:
                        print(e)
                        b = await response.content.read()
                        result = b.decode("utf-8")
                        raise DriverError(f'Error: {result}')
                else:
                    raise DriverError(f'Error: {response.text()}')

    def setup_legacy_request(self):
        self.auth_type = 'basic'
        self._user = self.api_username
        self._pwd = self.api_password
        self.auth = True
        self._legacy_call = True

    def setup_bearer_token_request(self, JWT):
        # pass
        self.auth_type = 'api_key'
        self.auth = {'Authorization' : 'Bearer ' + JWT}

    async def people(self):
        """people
        
        Get all the people for a given customer.
        """
        self.url = self.url + 'customers/{customer_id}/search/people'
        self.type = 'people'

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def person(self):
        """person
        
        Get a single person by id for a given customer.
        """
        self.url = self.url + 'customers/{customer_id}/people/{person_id}'
        self.type = 'person'

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['person_id'] = self._params['person_id']
            del self._params['person_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Person ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise
    
    async def jobs(self):
        """jobs
        
        Get a all jobs for a given customer in a portal.
        """
        self.url = self.url + 'customers/{customer_id}/search/portals/{portal_id}'
        self.type = 'jobs'

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['portal_id'] = self._params['portal_id']
            del self._params['portal_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Portal ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise
    
    async def job(self):
        """jobs
        
        Get a job by id for a given customer in a portal.
        """
        self.url = self.url + 'customers/{customer_id}/portals/{portal_id}/{job_id}'
        self.type = 'job'

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['portal_id'] = self._params['portal_id']
            del self._params['portal_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Portal ID")

        try:
            self._args['job_id'] = self._params['job_id']
            del self._params['job_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Job ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def jobs_filters(self):
        """jobs filters
        
        Get a all jobs filters for a given customer in a portal.
        """
        self.url = self.url + 'customers/{customer_id}/search/portals/{portal_id}/filters'
        self.type = 'jobs_filters'

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['portal_id'] = self._params['portal_id']
            del self._params['portal_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Portal ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def get_next_result(self, result, resource):
        """get next result
        
        Get the next pages of a given resource.
        """
        self.headers['Content-Type'] = 'application/json'
        r = result['searchResults']
        next = True
        limit = 1
        current = 0
        while next is True and (current < limit or self._is_test is not True):
            current+= 1
            print('Fetching next result, page:', current)
            last_id = r[-1]['id']
            data = {
                'filters': [
                    {
                        'name': resource + '.id',
                        'value': [
                            str(last_id)
                        ],
                        'operator': '>'
                    },
                ]
            }
            data = json.dumps(data)
            try:
                res, error = await self.request(self.url, method='POST', data=data)
                if error:
                    print('ICIMS: Error: Error getting next result', error)
                    next = False
                    break
                if 'searchResults' in res and len(res['searchResults']) > 0:
                    r = r + res['searchResults']
                else:
                    next = False
                    break
            except Exception as err:
                print(err)
                next = False
        del self.headers['Content-Type']
        return r

    async def get_next_result_v2(self, result):
        """get next result
        
        Get the next pages of a given stream data subscription.
        """
        r = result['events']
        next = True
        limit = 1
        current = 0
        while next is True and (current < limit or self._is_test is not True):
            current+= 1
            print('Fetching next result, page:', current)
            if 'lastEvaluatedKey' in result and result['lastEvaluatedKey'] != '':
                last_id = result['lastEvaluatedKey']
            else: break

            self._params['exclusiveStartKey'] = last_id
            try:
                res, error = await self.request(self.url)
                if error:
                    print('ICIMS: Error: Error getting next result', error)
                    next = False
                    break
                if 'events' in res and len(res['events']) > 0:
                    r = r + res['events']
                else:
                    next = False
                    break
            except Exception as err:
                print(err)
                next = False
        if 'exclusiveStartKey' in self._params:
            del self._params['exclusiveStartKey']
        return r

    async def process_result_list(self, result, resource, resource_url):
        """process result list
        
        Process results list and get each item details.
        """
        r = list()
        next = True
        limit = 3
        current = 0
        while next is True and (current < limit or self._is_test is not True):
            print('Fetching next item:', current)
            try:
                id = result[current]['id']
                self._args[resource + '_id'] = id
                url = self.original_url + resource_url
                url = self.build_url(url, args=self._args)
                res, error = await self.request(url, method='GET')
                if error:
                    print('ICIMS: Error: Error getting next item', error)
                    next = False
                    break
                if 'errors' in res:
                    next = False
                    break
                res['id'] = id
                r.append(res)

            except Exception as err:
                print(err)
                next = False
            current+= 1
        return r

    # Stream API

    async def stream_ids(self):
        """stream ids
        
        Get the stream ids.
        """

        str_len = len('{customer_id}/')
        self.url = self.stream_url[:-str_len]

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:    
            logging.exception(err)
            raise

    async def stream_subscriptions(self):
        """stream subscriptions
        
        Get the subscription stream ids for a given customer.
        """
        self.url = self.stream_url
        self.url = self.url + 'subscriptions'

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:    
            logging.exception(err)
            raise

    async def stream_data(self):
        """stream data
        
        Get a subscription stream data by subscription id.
        """
        self.url = self.stream_url
        self.url = self.url + 'subscriptions/{subscription_id}/events'

        try:
            self._args['subscription_id'] = self._params['subscription_id']
            del self._params['subscription_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Subscription ID")

        try:
            self._args['customer_id'] = self._params['customer_id']
            del self._params['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def query(self):
        """Query.

        Basic Query of ICIMS API.
        """
        self._result = None
        # initial connection
        await self.prepare_connection()
        # get the credentials
        try:
            # TODO Refactor this to call the token only when its stream api
            if self._legacy_call is False:
                jwt = await self.get_token()
                self.setup_bearer_token_request(jwt)
        except Exception as err:
            print(err)
            logging.error(f'ICIMS: Error getting token: {err!s}')
        try:
            result = await super().query()
            if self.type == 'jobs':
                self._result = await self.get_next_result(result, 'job')
                # self._result = await self.process_result_list(self._result, 'job', 'customers/{customer_id}/portals/{portal_id}/{job_id}')
            if self.type == 'people':
                self._result = await self.get_next_result(result, 'person')
                # self._result = await self.process_result_list(self._result, 'person', 'customers/{customer_id}/people/{person_id}')
            if self.type == 'stream_data':
                self._result = await self.get_next_result_v2(result)

            return self._result
        except Exception as err:
            print(err)
            logging.error(f'ICIMS: Error: {err!s}')
