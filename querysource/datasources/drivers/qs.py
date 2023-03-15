from datamodel import Field
from .abstract import DataDriver


class qsDriver(DataDriver):
    driver: str = 'querysource'
    driver_path: str = 'querysource.drivers.{driver}'
    slug: str = Field(required=True) # TODO: validate with slugify
    datasource: str = Field(required=True, default='db') # default database
