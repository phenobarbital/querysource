# cython: language_level=3, embedsignature=True, boundscheck=False, wraparound=True, initializedcheck=False
# Copyright (C) 2018-present Jesus Lara
#
"""
Main Functions for QuerySource.
"""
import os
import re
import builtins
import hashlib
import binascii
import requests
from numbers import Number
from pathlib import PurePath, Path
from libcpp cimport bool as bool_t
from urllib.parse import urlparse
from dateutil import parser
from dateutil.relativedelta import relativedelta
from cpython cimport datetime
from cpython.datetime cimport datetime as dt
from cpython.datetime cimport time as dtime
from zoneinfo import ZoneInfo
from uuid import UUID
from pandas import DataFrame
from .validators import is_udf


# hash utilities
cpdef object generate_key():
    return binascii.hexlify(os.urandom(20)).decode()


cpdef object get_hash(object value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


cpdef object trim(object value):
    if isinstance(value, str):
        return value.strip()
    else:
        return value


cpdef object plain_uuid(object obj):
    return str(obj).replace("-", "")


cpdef object to_uuid(object obj):
    try:
        return UUID(obj)
    except ValueError:
        return None

### Date-time Functions
# date utilities
cpdef datetime.date current_date(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    return dt.now(zone).date()


get_current_date = current_date


cpdef datetime.datetime current_timestamp(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    return dt.now(zone)

cpdef str today(str mask = "%m/%d/%Y", tz: str = None):
    try:
        if tz is not None:
            zone = ZoneInfo(key=tz)
        else:
            zone = ZoneInfo("UTC")
        return dt.now(zone).strftime(mask)
    except Exception as err:
        raise

cpdef int current_year():
    return dt.now().year

cpdef int previous_year():
    return (dt.now() - datetime.timedelta(weeks=52))


cpdef str previous_month(str mask = "%m/%d/%Y", int months = 1):
    return (dt.now() - relativedelta(months=months)).strftime(mask)


cpdef datetime.datetime first_day_of_month(object value = None, str zone = None):
    if zone is not None:
        tz = ZoneInfo(key=zone)
    else:
        tz = ZoneInfo("UTC")
    if not value:
        value = dt.now(tz)
    else:
        if value == 'current_date' or value == 'now':
            value = dt.now(tz)
        elif value == 'yesterday':
            value = dt.now(tz) - datetime.timedelta(days=1)
        elif value == 'tomorrow':
            value = dt.now(tz) + datetime.timedelta(days=1)
        elif isinstance(value, str): # conversion from str
            value = parser.parse(value)
    return value.replace(day=1)

cpdef str fdom(object value = None, str mask = "%Y-%m-%d", str zone = None):
    return first_day_of_month(value, zone=zone).strftime(mask)


cpdef datetime.datetime last_day_of_month(object value = None, str zone = None):
    if zone is not None:
        tz = ZoneInfo(key=zone)
    else:
        tz = ZoneInfo("UTC")
    if not value:
        value = dt.now(tz)
    else:
        if value == 'current_date' or value == 'now':
            value = dt.now(tz)
        elif value == 'yesterday':
            value = dt.now(tz) - datetime.timedelta(days=1)
        elif value == 'tomorrow':
            value = dt.now(tz) + datetime.timedelta(days=1)
        elif isinstance(value, str): # conversion from str
            value = parser.parse(value)
    return (value + relativedelta(day=31))

cpdef str ldom(object value = None, str mask = "%Y-%m-%d", str zone = None):
    return last_day_of_month(value, zone=zone).strftime(mask)


cpdef datetime.datetime last_day_of_previous_month(object value = None, str zone = None):
    if zone is not None:
        tz = ZoneInfo(key=zone)
    else:
        tz = ZoneInfo("UTC")
    if not value:
        value = dt.now(tz)
    else:
        if value == 'current_date' or value == 'now':
            value = dt.now(tz)
        elif value == 'yesterday':
            value = dt.now(tz) - datetime.timedelta(days=1)
        elif value == 'tomorrow':
            value = dt.now(tz) + datetime.timedelta(days=1)
        elif isinstance(value, str): # conversion from str
            value = parser.parse(value)
    first = value.replace(day=1)
    return (first - datetime.timedelta(days=1))

cpdef str ldopm(object value = None, str mask = "%Y-%m-%d", str zone = None):
    return last_day_of_previous_month(value, zone=zone).strftime(mask)


cpdef datetime.datetime now(str zone = None):
    if zone is not None:
        tz = ZoneInfo(key=zone)
    else:
        tz = ZoneInfo("UTC")
    return dt.now(tz)

cpdef str yesterday(str mask = '%Y-%m-%d'):
    return (datetime.datetime.now() - datetime.timedelta(1)).strftime(mask)


cpdef datetime.datetime yesterday_timestamp(str zone = None):
    if zone is not None:
        tz = ZoneInfo(key=zone)
    else:
        tz = ZoneInfo("UTC")
    return (dt.now(tz) - datetime.timedelta(1))


cpdef int current_month():
    return dt.now().month

cpdef datetime.datetime a_visit(datetime.datetime value = None, int offset = 30, str offset_type = 'minutes'):
    if not value:
        value = dt.utcnow()
    args = {offset_type: offset}
    return value + datetime.timedelta(**args)

offset_date = a_visit

cpdef datetime.datetime due_date(datetime.datetime value = None, int days = 1):
    if not value:
        value = dt.utcnow()
    return value + datetime.timedelta(days=days)


cpdef str date_after(datetime.datetime value = None, str mask = "%m/%d/%Y", int offset = 1, str offset_type = 'seconds'):
    if not value:
        value = dt.utcnow()
    args = {offset_type: int(offset)}
    return (value + datetime.timedelta(**args)).strftime(mask)


cpdef str date_ago(datetime.datetime value = None, str mask = "%m/%d/%Y", int offset = 1, str offset_type = 'seconds'):
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 1
    if not value:
        value = dt.utcnow()
    args = {offset_type: offset}
    return (value - datetime.timedelta(**args).strftime(mask))


cpdef str days_ago(str mask = "%m/%d/%Y", int offset = 1):
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 1
    return (dt.now() - datetime.timedelta(days=offset)).strftime(mask)


cpdef object year(str value, str mask = "%Y-%m-%d %H:%M:%S"):
    if value:
        try:
            newdate = parser.parse(value)
            return newdate.date().year
        except ValueError:
            d = value[:-4]
            d = dt.strptime(d, mask)
            return d.date().year
    else:
        return None

cpdef str first_dow(object value = None, str mask = '%Y-%m-%d'):
    if not value:
        value = dt.now()
    elif value == 'current_date' or value == 'now':
        value = dt.now()
    elif value == 'yesterday':
        value = dt.now() - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now() + datetime.timedelta(days=1)
    fdow = (value - datetime.timedelta(value.weekday()))
    return fdow.strftime(mask)


cpdef object month(str value, str mask = "%Y-%m-%d %H:%M:%S"):
    if value:
        try:
            newdate = parser.parse(value)
            return newdate.date().month
        except ValueError:
            a = value[:-4]
            a = dt.strptime(a, mask)
            return a.date().month
    else:
        return None


cpdef object get_last_week_date(str mask = "%Y-%m-%d"):
    _today = dt.utcnow()
    offset = (_today.weekday() - 5) % 7
    last_saturday = _today - datetime.timedelta(days=offset)
    return last_saturday.strftime(mask)


cpdef object to_midnight(object value, str mask = "%Y-%m-%d"):
    midnight = dt.combine(
        (value + datetime.timedelta(1)), dt.min.time()
    )
    return midnight.strftime(mask)

cpdef datetime.datetime epoch_to_date(int value, str tz = None):
    if isinstance(value, int):
        s, _ = divmod(value, 1000.0)
        if tz is not None:
            zone = ZoneInfo(key=tz)
        else:
            zone = ZoneInfo("UTC")
        return dt.fromtimestamp(s, zone)
    else:
        return None

cpdef long date_to_epoch(object value = None, str tz = None, bool_t with_miliseconds = True):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    if not value:
        value = dt.now(zone)
    elif value == 'current_date' or value == 'now':
        value = dt.now(zone)
    elif value == 'yesterday':
        value = dt.now(zone) - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now(zone) + datetime.timedelta(days=1)
    epoch = value.timestamp()
    if with_miliseconds is True:
        return epoch * 1000
    return epoch

to_epoch = date_to_epoch

cpdef str format_date(object value, str mask = "%Y-%m-%d %H:%M:%S", str expected_mask = "%Y-%m-%d"):
    """
    format_date.
        Convert an string into date an return with other format
    """
    if value == 'current_date' or value == 'now':
        value = dt.now()
    elif value == 'yesterday':
        value = dt.now() - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now() + datetime.timedelta(days=1)
    if isinstance(value, datetime.datetime):
        return value.strftime(mask)
    else:
        try:
            d = dt.strptime(str(value), expected_mask)
            return d.strftime(mask)
        except (TypeError, ValueError) as err:
            raise ValueError(err) from err

cpdef datetime.datetime to_date(object value, str mask="%Y-%m-%d %H:%M:%S", object tz = None):
    if value == 'current_date' or value == 'now':
        value = dt.now()
    elif value == 'yesterday':
        value = dt.now() - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now() + datetime.timedelta(days=1)
    if isinstance(value, datetime.datetime):
        return value
    else:
        try:
            result = dt.strptime(str(value), mask)
            if tz is not None:
                zone = ZoneInfo(key=tz)
            else:
                zone = ZoneInfo("UTC")
            if zone is not None:
                result = result.replace(tzinfo=zone)
            return result
        except (TypeError, ValueError, AttributeError):
            return parser.parse(str(value))


cpdef datetime.time to_time(object value = None, str mask= "%H:%M:%S"):
    if value is None:
        return dt.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if isinstance(value, datetime.datetime):
        return value.time()
    else:
        if len(str(value)) < 6:
            value = str(value).zfill(6)
        try:
            return dt.strptime(str(value), mask)
        except ValueError:
            return parser.parse(str(value)).time()


cpdef datetime.datetime build_date(object value, object mask = "%Y-%m-%d %H:%M:%S"):
    if isinstance(value, list):
        dt = to_date(value[0], mask=mask[0])
        mt = to_time(value[1], mask=mask[1]).time()
        return datetime.datetime.combine(dt, mt)
    elif isinstance(value, datetime.datetime):
        return value
    else:
        if value == 0:
            return datetime.datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            return datetime.datetime.strptime(str(value), mask)

cpdef str date_diff(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d", str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    if value == 'current_date' or value == 'now' or value == 'today':
        value = dt.now(zone)
    elif value == 'yesterday':
        value = dt.now(zone) - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now(zone) + datetime.timedelta(days=1)
    arg = {
        mode: int(diff)
    }
    return (value - datetime.timedelta(**arg)).strftime(mask)

cpdef str date_sum(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d"):
    if value == 'current_date' or value == 'now' or value == 'today':
        value = datetime.datetime.now()
    type = {
        mode: int(diff)
    }
    delta = datetime.timedelta(**type)
    if delta:
        return (value + delta).strftime(mask)
    else:
        return (value).strftime(mask)

cpdef datetime.datetime yesterday_midnight(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    midnight = dt.combine(
        dt.now(tz) - datetime.timedelta(1), dt.min.time()
    )
    return midnight

cpdef str midnight_yesterday(str mask = "%Y-%m-%d %H:%M:%S", str tz = None):
    return yesterday_midnight(tz=tz).strftime(mask)

cpdef datetime.datetime tomorrow_midnight(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    midnight = dt.combine(
        dt.now(tz) + datetime.timedelta(1), dt.min.time()
    )
    return midnight

cpdef str midnight_tomorrow(str mask = "%Y-%m-%d %H:%M:%S", str tz = None):
    return tomorrow_midnight(tz=tz).strftime(mask)


cpdef datetime.datetime current_midnight(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    midnight = dt.combine(
        dt.now(tz), dt.min.time()
    )
    return midnight

cpdef str midnight_current(str mask="%Y-%m-%dT%H:%M:%S", str tz = None):
    return current_midnight(tz=tz).strftime(mask)

midnight = midnight_current

cpdef object date_dow(object value = None, str day_of_week = 'monday', str mask = None, str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    if not value:
        today = dt.now(zone)
    if value == 'current_date' or value == 'now':
        today = dt.now(zone)
    elif value == 'yesterday':
        today = dt.now(zone) - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        today = dt.now(zone) + datetime.timedelta(days=1)
    elif isinstance(value, (datetime.date, datetime.datetime)):
        today = value
    try:
        dows = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2,
            'thursday': 3, 'friday': 4, 'saturday': 5,
            'sunday': 6
        }
        dw = today.weekday()
        dow = today - datetime.timedelta(days=(dw - dows[day_of_week]))
        if not mask:
            return dow
        else:
            return dow.strftime(mask)
    except Exception:
        return None


cpdef object date_diff_dow(object value = None, str day_of_week = 'monday', str mask = None, str tz = None, int diff = 0):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    if not value:
        today = dt.now(zone)
    if value == 'current_date' or value == 'now':
        today = dt.now(zone)
    elif value == 'yesterday':
        today = dt.now(zone) - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        today = dt.now(zone) + datetime.timedelta(days=1)
    elif isinstance(value, (datetime.date, datetime.datetime)):
        today = value
    try:
        dows = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2,
            'thursday': 3, 'friday': 4, 'saturday': 5,
            'sunday': 6
        }
        dw = today.weekday()
        dow = today - datetime.timedelta(days=(dw - dows[day_of_week]))
        delta = dow - datetime.timedelta(days=(diff))
        if mask:
            return delta.strftime(mask)
        else:
            return delta
    except Exception:
        return None

### string functions:
cpdef str extract_string(object value, str exp = r"_((\d+)_(\d+))_", int group = 1, bool_t parsedate = False):
    match = re.search(r"{}".format(exp), value)
    if match:
        result = (
            match.group(group)
            if not parsedate else parser.parse(match.group(group))
        )
        return result


cpdef bool_t uri_exists(str uri, int timeout = 2):
    """uri_exists.
    Check if an URL is reachable.
    """
    try:
        path = urlparse(uri)
    except ValueError:
        raise ValueError('Uri exists: Invalid URL')
    url = f'{path.scheme!s}://{path.netloc!s}'
    response = requests.get(url, stream=True, timeout=timeout)
    if response.status_code == 200:
        return True
    else:
        return False

### numeric functions
cpdef float to_percent(object value, int rounding = 2):
    return round(float(value) * 100.0, rounding)

cpdef object truncate_decimal(object value):
    if isinstance(value, Number):
        head, _, _ = value.partition('.')
        return head
    elif isinstance(value, (int, str)):
        try:
            val = float(value)
            head, _, _ = value.partition('.')
            return head
        except Exception:
            return None
    else:
        return None

### Filename Operations:
cpdef str filename(object path):
    if isinstance(path, PurePath):
        return path.name
    else:
        return os.path.basename(path)


cpdef str file_extension(object path):
    if isinstance(path, PurePath):
        return path.suffix
    else:
        return os.path.splitext(os.path.basename(path))[1][1:].strip().lower()


## UDF parser
def to_udf(str value, *args, **kwargs):
    """Executes an UDF function and returns result.
    """
    fn = None
    f = value.lower()
    if is_udf(value) is True:
        fn = globals()[f](*args, **kwargs)
    else:
        func = globals()[f]
        if not func:
            try:
                func = getattr(builtins, f)
            except AttributeError:
                return None
        if callable(func):
            try:
                fn = func(*args, **kwargs)
            except Exception:
                raise
        else:
            raise RuntimeError(
                f"to_udf Error: There is no Function called {fn!r}"
            )
    return fn

cpdef bool_t check_empty(object obj):
    """check_empty.
    Check if a basic object is empty or not.
    """
    if isinstance(obj, DataFrame):
        return True if obj.empty else False
    else:
        return bool(not obj)
