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
import calendar
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


cpdef dict empty_dict(object obj):
    if obj is None:
        return {}
    return obj

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

cdef inline datetime.datetime first_day_of_week(object value=None, str zone=None):
    cdef datetime.datetime date_value = datetime.datetime.now() if value is None else value
    cdef int days_since_start_of_week = date_value.weekday()  # Monday is 0
    return date_value - datetime.timedelta(days=days_since_start_of_week)

cdef inline datetime.datetime last_day_of_week(object value=None, str zone=None):
    """Function to calculate the last day of the week"""
    cdef datetime.datetime date_value = datetime.datetime.now() if value is None else value
    cdef int days_until_end_of_week = 6 - date_value.weekday()  # Sunday is 6
    return date_value + datetime.timedelta(days=days_until_end_of_week)

cpdef str fdow(object value=None, str mask="%Y-%m-%d", str zone=None):
    return first_day_of_week(value, zone=zone).strftime(mask)

cpdef str ldow(object value=None, str mask="%Y-%m-%d", str zone=None):
    return last_day_of_week(value, zone=zone).strftime(mask)

cdef inline datetime.datetime last_year_date(object value=None, str zone=None):
    """Function to calculate the same date last year"""
    cdef datetime.datetime date_value = datetime.datetime.now() if value is None else value
    try:
        return date_value.replace(year=date_value.year - 1)
    except ValueError:
        # Handle leap year edge cases (e.g., Feb 29 → Feb 28)
        return date_value.replace(year=date_value.year - 1, day=28)

cpdef str last_year(object value=None, str mask="%Y-%m-%d", str zone=None):
    return last_year_date(value, zone=zone).strftime(mask)

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


cpdef str last_dow(object value = None, str mask = '%Y-%m-%d'):
    """
    Get the last day of the week (Sunday) for the given date or reference point.

    Parameters:
        value: Input date or reference keyword ('current_date', 'now', 'yesterday', 'tomorrow').
        Defaults to 'now' if None.
        mask: The output format for the date as a string (default is '%Y-%m-%d').

    Returns:
        str: The last day of the week formatted as per the given mask.
    """
    if not value:
        value = dt.now()
    elif value == 'current_date' or value == 'now':
        value = dt.now()
    elif value == 'yesterday':
        value = dt.now() - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now() + datetime.timedelta(days=1)

    # Calculate last day of the week
    ldow = value + datetime.timedelta(days=(6 - value.weekday()))
    return ldow.strftime(mask)


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
    """
    date_diff.
        Calculate the difference between two dates.
        Supports different modes like 'days', 'weeks','months', 'years'
        The input date can be a datetime object, a string in the given mask, or a reference keyword ('current_date', 'now', 'yesterday', 'tomorrow', 'fdom', 'ldom', 'fdcw').
        The output date is always in the given mask.

    Parameters:
        value: The input date or reference keyword.
        diff: The difference to calculate.
        mode: The mode of the difference ('days', 'weeks', 'months', 'years').
        mask: The output format for the date as a string (default is '%Y-%m-%d').
        tz: The timezone to use for the calculation (default is 'UTC').
    Returns:
        str: The calculated date difference formatted as per the given mask.
    """
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
    elif value == 'fdom':  # First day of month
        now = dt.now(zone)
        value = now.replace(day=1)
    elif value == 'ldom':  # Last day of month
        now = dt.now(zone)
        last_day = calendar.monthrange(now.year, now.month)[1]
        value = now.replace(day=last_day)
    elif value == 'fdcw':  # First day of current week (assuming Monday as first day)
        now = dt.now(zone)
        value = now - datetime.timedelta(days=now.weekday())
    else:
        if not isinstance(value, (datetime.datetime, datetime.date)):
            try:
                value = parser.parse(value)
            except ValueError:
                return None
    arg = {
        mode: int(diff)
    }
    return (value - datetime.timedelta(**arg)).strftime(mask)


cpdef str date_sum(object value, int diff = 1, str mode = 'days', str mask = "%Y-%m-%d", str tz = None):
    """
    date_sum.
        Calculate the sum of two dates.
        Supports different modes like 'days', 'weeks','months', 'years'
        The input date can be a datetime object, a string in the given mask, or a reference keyword ('current_date', 'now', 'yesterday', 'tomorrow', 'fdom', 'ldom', 'fdcw').
        The output date is always in the given mask.
        If the sum exceeds the maximum or minimum date supported by the datetime module, it will be adjusted to the nearest valid date.
        If the sum is less than the minimum date supported by the datetime module, it will be adjusted to the nearest valid date.
        If the sum is greater than the maximum date supported by the datetime module, it will be adjusted to the nearest valid date.
        If the sum is a leap year and the mode is 'days', it will be adjusted to the next valid date.
        If the sum is a leap year and the mode is 'weeks', it will be adjusted to the next valid date.
        If the sum is a leap year and the mode is'months', it will be adjusted to the next valid date.
        If the sum is a leap year and the mode is 'years', it will be adjusted to the next valid date.
        If the sum is not a leap year and the mode is 'days', it will be adjusted to the previous valid date.
        If the sum is not a leap year and the mode is 'weeks', it will be adjusted to the previous valid date.

    Parameters:
        value: The input date or reference keyword.
        diff: The difference to add.
        mode: The mode of the sum ('days', 'weeks','months', 'years').
        mask: The output format for the date as a string (default is '%Y-%m-%d').
        tz: The timezone to use for the calculation (default is 'UTC').
    Returns:
        str: The calculated date sum formatted as per the given mask.
    """
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")

    # Determine the base date depending on the input token
    if value in ['current_date', 'now', 'today']:
        value = dt.now(zone)
    elif value == 'yesterday':
        value = dt.now(zone) - datetime.timedelta(days=1)
    elif value == 'tomorrow':
        value = dt.now(zone) + datetime.timedelta(days=1)
    elif value == 'fdom':  # First day of month
        now = dt.now(zone)
        value = now.replace(day=1)
    elif value == 'ldom':  # Last day of month
        now = dt.now(zone)
        import calendar
        last_day = calendar.monthrange(now.year, now.month)[1]
        value = now.replace(day=last_day)
    elif value in ('fdcw', 'first_day_of_week'):  # First day of current week (assuming Monday as first day)
        now = dt.now(zone)
        value = now - datetime.timedelta(days=now.weekday())
    else:
        if not isinstance(value, (datetime.datetime, datetime.date)):
            try:
                value = parser.parse(value)
            except ValueError:
                return None

    # Adjust the date by adding the time delta specified by diff and mode
    arg = { mode: int(diff) }
    return (value + datetime.timedelta(**arg)).strftime(mask)


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


cpdef int current_weekday(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    return dt.now(zone).weekday()

cpdef int current_day_of_week(str tz = None):
    if tz is not None:
        zone = ZoneInfo(key=tz)
    else:
        zone = ZoneInfo("UTC")
    return dt.now(zone).isoweekday()


cpdef long current_epoch(bint milliseconds=False, str tz=None):
    """
    Returns the current time as a UNIX epoch.

    Parameters:
        milliseconds (bool): If True, the epoch is returned in milliseconds.
        tz (str): A timezone string key; if None, defaults to UTC.

    Returns:
        int: UNIX epoch timestamp (seconds or milliseconds).
    """
    dt_obj = current_timestamp(tz)
    epoch_val = dt_obj.timestamp()
    if milliseconds:
        return int(epoch_val * 1000)
    else:
        return int(epoch_val)


cpdef long fdow_epoch(object value=None, bint milliseconds=False, str zone=None):
    """
    Returns the first day of the week as a UNIX epoch timestamp.

    Parameters:
        value: Optional datetime input used by first_day_of_week.
        milliseconds (bool): If True, returns the timestamp in milliseconds.
        zone (str): Timezone string key; if None, defaults to UTC as per the helper.

    Returns:
        int: UNIX epoch timestamp (seconds or milliseconds).
    """
    dt_obj = first_day_of_week(value, zone=zone)
    epoch_val = dt_obj.timestamp()
    if milliseconds:
        return int(epoch_val * 1000)
    else:
        return int(epoch_val)


cpdef long ldow_epoch(object value=None, bint milliseconds=False, str zone=None):
    """
    Returns the last day of the week as a UNIX epoch timestamp.

    Parameters:
        value: Optional datetime input used by last_day_of_week.
        milliseconds (bool): If True, returns the timestamp in milliseconds.
        zone (str): Timezone string key; if None, defaults to UTC as per the helper.

    Returns:
        int: UNIX epoch timestamp (seconds or milliseconds).
    """
    dt_obj = last_day_of_week(value, zone=zone)
    epoch_val = dt_obj.timestamp()
    if milliseconds:
        return int(epoch_val * 1000)
    else:
        return int(epoch_val)


cpdef long yesterday_epoch(bint milliseconds=False, str tz=None):
    """
    Returns yesterday's midnight as a UNIX epoch timestamp.

    Parameters:
        milliseconds (bool): If True, returns the timestamp in milliseconds.
        tz (str): A timezone string key; if None, defaults to UTC.

    Returns:
        int: UNIX epoch timestamp (seconds or milliseconds).
    """
    dt_obj = yesterday_midnight(tz)
    epoch_val = dt_obj.timestamp()
    if milliseconds:
        return int(epoch_val * 1000)
    else:
        return int(epoch_val)


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
