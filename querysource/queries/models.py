from typing import Union, Optional, Any
from datetime import datetime, timezone
from google.cloud.bigquery.table import RowIterator
from datamodel import BaseModel, Field
from ..datasources.drivers import SUPPORTED

def supported_drivers(field, driver, **kwargs):  # pylint: disable=W0613
    return driver in SUPPORTED


class Query(BaseModel):
    """Represents the entry of a query to be executed.
    """
    driver: str = Field(required=False, default='pg', validator=supported_drivers)
    datasource: str = Field(required=False, default=None)
    query: Union[str, dict] = Field(required=False)  # TODO: to be validated
    arguments: list = Field(required=False, default_factory=list)
    parameters: dict = Field(required=False, default_factory=dict)
    retrieved: datetime = Field(required=False, default=datetime.now(timezone.utc))
    raw_result: bool = Field(default=False)
    queued: bool = Field(default=False)
    connection: Optional[Any] = Field(required=False)

    class Meta:
        strict = True


class QueryResult(BaseModel):
    driver: str = Field(required=False, default='pg')
    state: str = Field(required=False)
    query: str = Field(required=False, default=None)
    data: Union[list, dict] = Field(required=False, default_factory=list)
    duration: float = Field(required=False, default=None)
    errors: Optional[dict] = Field(required=False, default=None)

    class Meta:
        strict = True

    def __post_init__(self):
        if isinstance(self.data, RowIterator):
            # Convert BigQuery to list
            self.data = [dict(row) for row in self.data]
        return super().__post_init__()
