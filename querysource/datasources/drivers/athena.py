from datamodel import Field
from ...conf import (
    ATHENA_REGION,
    ATHENA_KEY,
    ATHENA_SECRET,
    ATHENA_BUCKET,
    ATHENA_SCHEMA,
)
from .abstract import AWSDriver


class athenaDriver(AWSDriver):
    driver: str = 'athena'
    name: str = 'Amazon Athena'
    bucket: str = Field(required=True)
    schema: str = Field(required=False, default='default')
    workgroup: str = Field(required=False, default='primary')

dynamodb_default = athenaDriver(
    region=ATHENA_REGION,
    access_key=ATHENA_KEY,
    secret_key=ATHENA_SECRET,
    bucket=ATHENA_BUCKET,
    schema=ATHENA_SCHEMA
)
