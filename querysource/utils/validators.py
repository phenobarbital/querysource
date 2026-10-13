import uuid
from datetime import datetime, timezone

import dateutil


### validations
def isdate(value):
    returned = False
    # can be a unix epoch?
    try:
        b = datetime.fromtimestamp(int(value), tz=timezone.utc)
        returned = bool(b)
    except Exception:
        pass
    try:
        dateutil.parser.parse(value)
        returned = True
    except Exception:
        pass
    return returned

is_date = isdate

def isinteger(value):
    return bool(isinstance(value, int))


def isnumber(value):
    return bool(isinstance(value, float) or isinstance(value, int))

def is_string(value):
    if type(value) is str:
        return True
    else:
        return False


def is_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False

def validate_type_uuid(value):
    try:
        uuid.UUID(value)
    except ValueError:
        pass


def is_boolean(value):
    if isinstance(value, bool):
        return True
    elif value == 'null' or value == 'NULL':
        return True
    elif value == 'true' or value == 'TRUE':
        return True
    else:
        return False

"""
PostgreSQL utilities
"""
PG_CONSTANTS = ["CURRENT_DATE", "CURRENT_TIMESTAMP"]


def is_pgconstant(value):
    return value in PG_CONSTANTS
