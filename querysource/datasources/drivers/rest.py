from datamodel import Field
from .abstract import CloudDriver

def valid_method(field, value): # pylint: disable=W0613
    return value in ('get', 'post', 'put', 'delete', 'patch')

def valid_auth_type(field, value): # pylint: disable=W0613
    return value in ('key', 'token', 'basic')

class restDriver(CloudDriver):
    """restDriver.

    Generic connection to an HTTP(s) RESTful API.
    """
    url: str = Field(required=False)
    method: str = Field(required=True, default='get', validator=valid_method)
    auth_type: str = Field(required=True, default='key', validator=valid_auth_type)
    token_type: str = Field(required=False, default='Bearer')
