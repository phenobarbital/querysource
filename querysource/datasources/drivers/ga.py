from datamodel import Field
from .abstract import GoogleDriver


class gaDriver(GoogleDriver):
    driver: str = 'ga'
    name: str = 'Google Analytics'
    property_id: str = Field(required=True)
