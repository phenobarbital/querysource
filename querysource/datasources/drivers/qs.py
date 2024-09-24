from datamodel import Field
from .abstract import BaseDriver


class qsDriver(BaseDriver):
    driver: str = 'querysource'
    driver_path: str = 'querysource.drivers.{driver}'
    slug: str = Field(required=True)  # TODO: validate with slugify
    datasource: str = Field(required=True, default='db')  # default datasource
