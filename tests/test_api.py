"""Tests.

Testing QuerySource.
"""
import pytest
import asyncio
from datetime import datetime
import timeit
from concurrent.futures import ThreadPoolExecutor
import aiohttp
from navconfig.logging import logging
from asyncdb.exceptions import default_exception_handler
from asyncdb.utils.functions import cPrint
from functools import partial
from itertools import repeat


DRIVER = 'postgres'
DSN = "postgres://qs_data:12345678@127.0.0.1:5432/navigator_dev"
params = {
    "host": '127.0.0.1',
    "port": '5432',
    "user": 'qs_data',
    "password": '12345678',
    "database": 'navigator_dev'
}

API_HOST = "http://nav-api.dev.local:5000/api/v2/services/queries/{slug}?refresh=1"

async def query_api(slug, ev):
    cPrint(
        f':: Running {slug}',
        level='INFO'
    )
    conditions = {
        "refresh": True
    }
    startTime = datetime.now()
    print(timeit.timeit('1 + 3 ', number=50000000))
    timeout = aiohttp.ClientTimeout(total=360)
    url = API_HOST.format(slug=slug)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=timeout
            ) as response:
                # check if result, empty or not found
                pytest.assume(response.status in (200, 204, 404))
                result = await response.json()
                pytest.assume(result is not None)
                print(f'{slug} count: {len(result)}')
                logging.info(f'{slug} count: {len(result)}')
    except Exception as err:
        raise Exception(err)
    finally:
        print(
            "Generated in: %s" % (datetime.now() - startTime)
        )
        return True

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(default_exception_handler)
    yield loop
    loop.close()

pytestmark = pytest.mark.asyncio

async def test_threads(event_loop):
    slugs = [
        'corporate_dashboard_adp',
        'turnover_corp_dashboard',
        'corporate_revenue_billed',
        'billable_expenses',
        'oportunities_lost',
        'oportunities_won',
        'pipeline_by_industry',
        'agent_monitoring'
    ]
    with ThreadPoolExecutor() as ex:
        args = ((s, event_loop) for s in slugs)
        for obj in ex.map(lambda p: query_api(*p), args):
            result = await obj
            pytest.assume(result is not None)
