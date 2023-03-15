from datamodel import Field
from .abstract import GoogleDriver


class gcalcDriver(GoogleDriver):
    driver: str = 'gcalc'
    name: str = 'Google Sheets'
    spreadsheet_id: str = Field(required=True)
