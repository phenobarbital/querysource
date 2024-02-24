from datamodel import Field
from ...conf import (
    JIRA_HOST,
    JIRA_USERNAME,
    JIRA_PASSWORD
)
from .abstract import CloudDriver


class jiraDriver(CloudDriver):
    url: str = Field(required=False, comment='JIRA URL')


try:
    jira_default = jiraDriver(
        url=JIRA_HOST,
        username=JIRA_USERNAME,
        password=JIRA_PASSWORD
    )
except ValueError:
    jira_default = None
