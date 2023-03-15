from typing import Any
from .rest import restSource
from simple_salesforce import Salesforce
from datetime import datetime, timedelta
from querysource.exceptions import DataNotFound

class salesforce(restSource):
    """Salesforce.

        Get data with SOQL requests
    """

    def __post_init__(
            self,
            definition: dict = None,
            conditions: dict = None,
            request: Any = None,
            **kwargs
    ) -> None:

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
                except (ValueError, AttributeError):
                    raise ValueError("Salesforce: Missing Password")

        try:
            self.type = definition.params['type']
        except (ValueError, AttributeError):
            self.type = None
        if 'type' in self._conditions:
            self.type = self._conditions['type']
            del self._conditions['type']

        # set parameters
        self._args = conditions

        self.sf = Salesforce(
            username=self._user,
            password=self._pwd,
            security_token=self.token,
            domain=self.domain)

        #for i in range(501, 1001):
        #    self.sf.Lead.create({'FirstName':f'FirstName {i}', 'LastName': f'LastName {i}', 'Status':'New'})
        #print(self.sf.Lead.describe())

    async def soql(self):
        res = self.sf.query_all(self._args['query'])
        self._result = res['records']
        return self._result
    
    async def all_fields(self):
        try:
            object = self._args['object']
        except (ValueError, AttributeError):
            raise ValueError("Salesforce: Missing Object Name")
        
        obj = getattr(self.sf, object)
        desc = obj.describe()
        fields = []
        for f in desc['fields']:
            fields.append(f['name'])
        str_fields = ', '.join(fields)
        query = f'SELECT {str_fields} FROM {object}'
        res = self.sf.query_all(query)
        self._result = res['records']
        return self._result
        

    async def query(self):
        if type == 'soql':
            return self.soql()
        if type== 'all_fields':
            return self.all()
