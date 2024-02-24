from datamodel import Field
from ...conf import (
    SALESFORCE_INSTANCE,
    SALESFORCE_TOKEN,
    SALESFORCE_DOMAIN,
    SALESFORCE_USERNAME,
    SALESFORCE_PASSWORD
)
from .abstract import CloudDriver


class salesforceDriver(CloudDriver):
    driver: str = 'salesforce'
    name: str = 'SalesForce'
    instance: str = Field(required=False)
    domain: str = Field(required=False)
    session_id: str = Field(required=False)

    def __post_init__(self, user, hostname, *args, **kwargs):  # pylint: disable=W0613,W0221
        if not self.instance:
            self.instance = self.url
        super(salesforceDriver, self).__post_init__(user, hostname, *args, **kwargs)

    def get_parameters(self) -> dict:
        return {
            # "instance": self.instance,
            "username": self.username,
            "password": self.password,
            "security_token": self.access_token,
            "domain": self.domain,
        }


if SALESFORCE_INSTANCE:
    salesforce_default = salesforceDriver(
        instance=SALESFORCE_INSTANCE,
        domain=SALESFORCE_DOMAIN,
        access_token=SALESFORCE_TOKEN,
        username=SALESFORCE_USERNAME,
        password=SALESFORCE_PASSWORD
    )
else:
    salesforce_default = None
