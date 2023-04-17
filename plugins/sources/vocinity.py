import json
import logging
import rapidjson
import aiohttp
import pytz
from datetime import datetime
from .rest import restSource
from querysource.exceptions import *


class vocinity(restSource):
    """
      Vocinity
        Getting Data from Vocinity servers.
    """

    base_url: str = 'https://api.vocinity.com'
    login_url = 'https://api.vocinity.com/public/auth/login'
    _token_type: str = 'Bearer'
    _saved_token: str = 'navigator_vocinity_token'
    pagination: int = 100
    caching: bool = True  # this component uses Redis

    def __init__(self, definition=None, params: dict = {}, **kwargs):
        self._token: str = None
        self._expiration = 3600
        super(vocinity, self).__init__(definition, params, **kwargs)

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError, KeyError):
            self.type = None

        try:
            self.type = self._params['type']
            del self._params['type']
        except (ValueError, AttributeError, KeyError):
            pass

        try:
            self.pagination = self._params['pagination']
            del self._params['pagination']
        except (KeyError, AttributeError):
            pass

        if 'type' in params:
            self.type = params['type']
            del params['type']

        # Credentials
        if 'email' in self._params:
            self.email = self._params['email']
            del self._params['email']
        else:
            self.email = self._env.get('VOCINITY_EMAIL')
            if not self.email:
                try:
                    self.email = definition.params['email']
                except (ValueError, AttributeError):
                    raise ValueError("Vocinity: Missing Email")

        if 'password' in self._params:
            self.password = self._params['password']
            del self._params['password']
        else:
            self.password = self._env.get('VOCINITY_PASSWORD')
            if not self.password:
                try:
                    self.password = definition.params['password']
                except (ValueError, AttributeError):
                    raise ValueError("Vocinity: Missing Password")

        try:
            self.agent = self._params['agent']
            del self._params['agent']
        except (ValueError, AttributeError, KeyError):
            self.agent = None

        if 'startdate' in self._params:
            try:
                tz = pytz.timezone('America/New_York')
                dt1 = datetime.strptime(self._params['startdate'], "%Y-%m-%dT%H:%M:%S")
                dt1 = tz.localize(dt1, is_dst=None)
                self._params['startdate'] = dt1.strftime("%Y-%m-%dT%H:%M:%S")

                # .replace(tzinfo=timezone.utc).isoformat()

                dt2 = datetime.strptime(self._params['enddate'], "%Y-%m-%dT%H:%M:%S")
                dt2 = tz.localize(dt2, is_dst=None)
                self._params['enddate'] = dt2.strftime("%Y-%m-%dT%H:%M:%S")

                self._startdate = self._params['startdate']
                self._enddate = self._params['enddate']

                # print('DATES: ', self._startdate, self._enddate)

            except Exception as err:
                logging.info("Vocinity API: wrong date format: {}".format(str(err)))
                self._startdate = None
                self._enddate = None
            finally:
                # remove unused conditions
                try:
                    del self._params['startdate']
                    del self._params['enddate']
                except (KeyError, ValueError, TypeError) as err:
                    pass

        # set parameters
        self._args = params

    async def get_token(self):
        result = None
        # get the redis connection
        try:
            await self._redis.connection()
        except Exception as err:
            logging.exception(f'REST Vocinity error: {err!s}')
            raise
        # try if is saved on redis:
        try:
            result = await self._redis.get(self._saved_token)
            if result:
                data = rapidjson.loads(result)
                logging.debug(':: Vocinity: Using credentials in Cache')
                self._token = data['token']
                return self._token
        except Exception as err:
            print(err)
            logging.exception(f'Vocinity Redis Error: {err!s}')
            raise
        # else: get token from URL
        data = {
            "email": self.email,
            "password": self.password
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
                        self._token = result['token']
                        try:
                            status = await self._redis.setex(
                                self._saved_token,
                                data,
                                self._expiration
                            )
                            print('Token Saved?:', status)
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

    async def agents(self):
        """
            agents.
            Returns a list of all agents from logged user.
        """
        self.method = 'GET'
        self.data = None
        self.url = self.base_url + '/agents'
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def conversation(self):
        """
            conversation.
            Get the details of a conversation for a given sessionID.
        """
        if 'session_id' in self._params:
            self.session_id = self._params['session_id']
        else:
            logging.exception('session_id not provided')
            raise ValueError("session_id not provided")
        self.method = 'GET'
        self.data = None
        self.url = self.base_url + '/agents/session/conversation?sessionID={}'.format(self.session_id)
        del self._params['session_id']
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    async def conversations(self):
        """
            conversations.
            Get the list of conversations.
        """
        if not self.agent:
            try:
                self.agent = self._params['agent']
                del self._params['agent']
            except (KeyError, ValueError):
                logging.exception('Agent ID was not provided')
                raise ValueError("Agent Id not provided")
        self.method = 'POST'
        self.headers['Content-Type'] = 'application/json'
        self.url = self.base_url + '/agents/{}/conversations'.format(self.agent)
        # getting filter dates:
        self.data = {
            'identifier': self.agent,
            "additional_attributes": ["tracker"],
            'start': 0,
            'length': self.pagination,
            'columns': [],
            'order': [],
            'search': {},
            'draw': 1
        }
        filters = {}
        if self._startdate is not None:
            filters = {
                "startTime": {
                    "gte":  self._startdate,
                    "lte": self._enddate
                }
            }
        # if len(self._params) > 0:
        #     filters = {**filters, **self._params}
        self.data['filters'] = filters
        print('VOCINITY FILTER: ', self.data)
        try:
            self._result = await self.query()
            return self._result
        except Exception as err:
            logging.exception(err)
            raise

    def setup_bearer_token_request(self, token):
        # pass
        self.auth_type = 'api_key'
        self.auth = {'Authorization': 'Bearer {}'.format(token)}

    async def query(self):
        """
            Query.
            Basic Query of Vocinity API.
        """
        self._result = None
        # initial connection
        await self.prepare_connection()
        # get the credentials
        try:
            jwt = await self.get_token()
            self.setup_bearer_token_request(jwt)
        except Exception as err:
            print(err)
            logging.error(f'Vocinity: Error getting token: {err!s}')
        try:
            if self.type == 'conversations':
                # and other endpoints with pagination.
                # pagination results:
                result = []
                print('Conversations URL', self.url)
                while True:
                    print('Start at:', self.data['start'])
                    try:
                        data = json.dumps(self.data)
                        res, error = await self.request(self.url, method=self.method, data=data)
                        if error:
                            print(error)
                            break
                        if res['data'] == []:
                            break
                        result += res['data']
                        self.data['start'] += self.pagination
                    except Exception as err:
                        print('VO ', err)
                        logging.exception(err)
                        raise
                self._result = result
                return self._result
            else:
                data = json.dumps(self.data)
                self._result, error = await self.request(self.url, method=self.method, data=data)
                # print(self._result, error)
            if error is not None:
                # print(error)
                logging.error(f'Vocinity: Error: {error!s}')
            elif not self._result:
                raise DataNotFound('Vocinity: No data was found')
            else:
                return self._result
        except DataNotFound as err:
            print(err)
            raise
        except Exception as err:
            print(err)
            logging.error(f'Vocinity: Error: {err!s}')
