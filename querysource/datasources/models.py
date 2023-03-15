"""
DataSource Model.
"""
import uuid
from datetime import datetime
from asyncdb.models import Model, Field, Column
from .drivers import SUPPORTED
from .drivers.abstract import DataDriver


def auto_now_add(*args, **kwargs): # pylint: disable=W0613
    return uuid.uuid4()

def get_default_program(**kwargs): # pylint: disable=W0613
    return 1

def supported_drivers(field, value): # pylint: disable=W0613
    return value in SUPPORTED

class DataSource(Model):
    """
    Datasource model for saving Datasources in databases.
    TODO: serialize object using pickle.
    """
    uid: uuid.UUID = Field(default=auto_now_add, required=True, primary_key=True, db_default='uuid_generate_v4()')
    driver: str = Field(required=True, validator=supported_drivers)
    name: str = Column(required=True)
    description: str
    params: dict = Column(required=False, default_factory=dict)
    credentials: dict = Field(required=False)
    dsn: str = Field(required=False)
    program_slug: str = Column(required=True, default=get_default_program)
    drv: DataDriver = Column(required=False, comment="Serialized version of the Driver.")
    created_at: datetime = Column(required=False, default=datetime.now(), db_default='now()')
    updated_at: datetime = Column(required=False)

    class Meta:
        name = 'datasources'
        schema = 'public'
        app_label = 'public'
        strict = True
        connection = None
