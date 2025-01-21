from typing import Any
from urllib.parse import urlencode
import aiohttp
from navconfig.logging import logging
from datamodel.parsers.json import json_encoder, json_decoder
from datamodel.parsers.encoders import DefaultEncoder
from querysource.exceptions import DataNotFound, ConfigError
from querysource.providers.sources import restSource


class icims(restSource):
    """
      ICIMS
        Getting Data from ICIMS servers.
    """

    url: str = 'https://api.icims.com/'
    original_url: str = 'https://api.icims.com/'  # Keep original url
    login_url = 'https://login.icims.com/oauth/token'
    stream_url: str = 'https://data-transfer-assembler.production.env.icims.tools/datastream/v2/streams/{customer_id}/'
    _token: str = None
    _expiration: int = 1800
    _token_type: str = 'Bearer'
    _caching: bool = True
    _saved_token: str = 'navigator_icims_token'
    _legacy_call: bool = False
    _is_test: bool = False
    use_redis: bool = True

    def __init__(
        self,
        definition: dict = None,
        conditions: dict = None,
        request: Any = None, **kwargs
    ):
        super(icims, self).__init__(definition, conditions, request, **kwargs)

        args = self._args
        self._conditions = conditions
        self.customer_id = conditions.get('customer_id', None)

        if not self.customer_id:
            self.customer_id = self._env.get('ICIMS_CUSTOMER_ID')

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

        # Credentials
        # Username and password:
        if 'api_username' in conditions:
            self.api_username = conditions['api_username']
            del conditions['api_username']
        else:
            self.api_username = self._env.get('ICIMS_API_USERNAME')
            if not self.api_username:
                try:
                    self.api_username = definition.params['api_username']
                except (ValueError, AttributeError) as exc:
                    raise ValueError(
                        "ICIMS: Missing API Username"
                    ) from exc

        if 'api_password' in conditions:
            self.api_password = conditions['api_password']
            del conditions['api_password']
        else:
            self.api_password = self._env.get('ICIMS_API_PASSWORD')
            if not self.api_password:
                try:
                    self.api_password = definition.params['api_password']
                except (ValueError, AttributeError):
                    pass

        if 'api_key' in conditions:
            self.client_id = conditions['api_key']
            del conditions['api_key']
        else:
            self.client_id = self._env.get('ICIMS_API_KEY')
            if not self.client_id:
                try:
                    self.client_id = definition.params['api_key']
                except (ValueError, AttributeError) as exc:
                    raise ValueError(
                        "ICIMS: Missing API Key"
                    ) from exc

        if 'api_secret' in conditions:
            self.client_secret = conditions['api_secret']
            del self._conditions['api_secret']
        else:
            self.client_secret = self._env.get('ICIMS_API_SECRET')
            if not self.client_secret:
                try:
                    self.client_secret = definition.params['api_secret']
                except (ValueError, AttributeError) as exc:
                    raise ValueError(
                        "ICIMS: Missing API Secret"
                    ) from exc

        self.original_url = self.url

        # check if the call is legacy
        if 'legacy' in conditions and conditions['legacy'] is True:
            self.setup_legacy_request()
            del conditions['legacy']

        # check if the request is a test (To only get limited records)
        if 'test' in conditions and conditions['test'] is True:
            self._is_test = True
            del conditions['test']

        # if types
        # if self.type == 'people':
        #     self.url = self.url + 'customers/{customer_id}/search/people'
        # elif self.type == 'person':
        #     self.url = self.url + 'customers/{customer_id}/people/{person_id}'

        if self.type == 'forms_list':
            self.setup_legacy_request()
            self.url = self.url + 'customers/{customer_id}/forms/list'

        if self.type == 'forms':
            self.setup_legacy_request()
            self.url = self.url + 'customers/{customer_id}/forms'

        # set parameters
        self._args = args

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
                data = json_decoder(result)
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
                        raise ConfigError(
                            f'Error: {result}'
                        ) from e
                else:
                    raise ConfigError(f'Error: {response.text()}')

    def setup_legacy_request(self):
        self.auth_type = 'basic'
        self._user = self.api_username
        self._pwd = self.api_password
        self.auth = {}
        self._legacy_call = True

    def setup_bearer_token_request(self, JWT):
        # pass
        self.auth_type = 'api_key'
        self.auth = {'Authorization': 'Bearer ' + JWT}

    async def people(self):
        """people

        Get all the people for a given customer.
        """
        self.url = self.url + 'customers/{customer_id}/search/people'
        self.type = 'people'

        try:
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
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
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['person_id'] = self._conditions['person_id']
            del self._conditions['person_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Person ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def forms_list(self):
        """forms.

        Get a list of Forms.
        """
        self.url = self.url + 'customers/{customer_id}/forms/list'
        self.type = 'forms_list'
        self._legacy_call = True
        self.method = 'get'
        try:
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
        except (KeyError, AttributeError) as exc:
            self._args['customer_id'] = self.customer_id
            if not self._args['customer_id']:
                raise ValueError(
                    "ICIMS: Missing Customer ID"
                ) from exc
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
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['portal_id'] = self._conditions['portal_id']
            del self._conditions['portal_id']
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
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['portal_id'] = self._conditions['portal_id']
            del self._conditions['portal_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Portal ID")

        try:
            self._args['job_id'] = self._conditions['job_id']
            del self._conditions['job_id']
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
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._args['portal_id'] = self._conditions['portal_id']
            del self._conditions['portal_id']
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
            current += 1
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
            data = json_encoder(data)
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
            current += 1
            print('Fetching next result, page:', current)
            if 'lastEvaluatedKey' in result and result['lastEvaluatedKey'] != '':
                last_id = result['lastEvaluatedKey']
            else:
                break
            self._conditions['exclusiveStartKey'] = last_id
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
        if 'exclusiveStartKey' in self._conditions:
            del self._conditions['exclusiveStartKey']
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
            current += 1
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
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
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
            self._args['subscription_id'] = self._conditions['subscription_id']
            del self._conditions['subscription_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Subscription ID")

        try:
            self._args['customer_id'] = self._conditions['customer_id']
            del self._conditions['customer_id']
        except (KeyError, AttributeError):
            raise ValueError("ICIMS: Missing Customer ID")

        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def query(self, url: str = None, params: dict = {}):
        """Query.

        Basic Query of ICIMS API.
        """
        self._result = None
        if url:
            self.url = self.build_url(url, queryparams=urlencode(params))
        # get the credentials
        try:
            # TODO Refactor this to call the token only when its stream api
            if self._legacy_call is False:
                jwt = await self.get_token()
                self.setup_bearer_token_request(jwt)
            else:
                self.setup_legacy_request()
        except Exception as err:
            print(err)
            logging.error(
                f'ICIMS: Error getting token: {err!s}'
            )
            raise
        try:
            result = await super().query()
            if self.type == 'jobs':
                self._result = await self.get_next_result(result, 'job')
            if self.type == 'people':
                self._result = await self.get_next_result(result, 'person')
            if self.type in ('forms'):
                self._result = await self.get_next_result(result, 'forms')
            if self.type == 'stream_data':
                self._result = await self.get_next_result_v2(result)
            elif self.type == 'forms_list':
                self._result = result
            if not self._result:
                raise DataNotFound(
                    "ICIMS: No ICIMS data was found."
                )
            return self._result
        except Exception as err:
            print(err)
            logging.error(f'ICIMS: Error: {err!s}')
