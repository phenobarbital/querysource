from querysource.conf import (
    DYNAMODB_REGION,
    DYNAMODB_KEY,
    DYNAMODB_SECRET
)
from .abstract import AWSDriver


class dynamodbDriver(AWSDriver):
    driver: str = 'dynamodb'
    name: str = 'DynamoDB'

dynamodb_default = dynamodbDriver(
    region=DYNAMODB_REGION,
    access_key=DYNAMODB_KEY,
    secret_key=DYNAMODB_SECRET
)
