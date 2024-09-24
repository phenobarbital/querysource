from typing import Optional
from pathlib import PurePath
from datamodel import Field
from ...conf import (
    JIRA_HOST,
    JIRA_USERNAME,
    JIRA_PASSWORD,
    JIRA_TOKEN,
    JIRA_CERT
)
from .abstract import CloudDriver


class jiraDriver(CloudDriver):
    driver: str = 'jira'
    description: str = 'Atlassian Jira'
    url: str = Field(required=False, comment='JIRA URL')
    url_schema: str = None
    basic_auth: tuple
    key_cert: Optional[PurePath] = Field(required=False)
    key_cert_data: str
    access_token_secret: str = Field(required=False)
    consumer_key: str = Field(required=False, default='jira-oauth-consumer')

    def __post_init__(self, *args, **kwargs):  # pylint: disable=W0613,W0221
        self.basic_auth = (self.username, self.access_token)
        if self.key_cert:
            with open(str(self.key_cert), 'r') as kcf:
                self.key_cert_data = kcf.read()
        super().__post_init__(*args, **kwargs)

    def params(self) -> dict:
        """params

        Returns:
            dict: params required for Jira.
        """
        if self.key_cert:
            oauth_dict = {
                'access_token': self.access_token,
                'access_token_secret': self.access_token_secret,
                'consumer_key': self.consumer_key,
                'key_cert': self.key_cert_data
            }
            return {
                "oauth": oauth_dict
            }
        return {
            "basic_auth": self.basic_auth
        }

try:
    jira_default = jiraDriver(
        url=JIRA_HOST,
        username=JIRA_USERNAME,
        password=JIRA_PASSWORD,
        access_token=JIRA_TOKEN,
        key_cert=JIRA_CERT
    )
except ValueError:
    jira_default = None
