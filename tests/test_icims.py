"""Tests.

Testing icims driver.
"""
import pytest
from asyncdb import AsyncDB, AsyncPool
import asyncio
from datetime import datetime
import timeit

from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource
from querysource.connections import QueryConnection
from querysource.utils.functions import cPrint

DRIVER = 'postgres'
DSN = "postgres://troc_pgdata:12345678@127.0.0.1:5432/navigator_dev"
params = {
    "host": '127.0.0.1',
    "port": '5432',
    "user": 'troc_pgdata',
    "password": '12345678',
    "database": 'navigator_dev'
}


async def myquery(qc, ev, slug='', conditions: dict = None):
    cPrint(
        f':: Running {slug}',
        level='INFO')
    driver = {'driver': 'rest', 'source': 'icims', 'method': slug}

    startTime = datetime.now()
    print(timeit.timeit('1 + 3 ', number=50000000))
    # redis = await qc.redis()
    connection = await qc.get_connection()
    pytest.assume(connection.is_connected() is True)
    qry = QuerySource(
        driver=driver,
        query_raw=None,
        conditions=conditions,
        loop=ev
    )
    pytest.assume(qry is not None)
    try:
        await qry.get_query()
    except Exception as err:
        pytest.fail(err)
    pytest.assume(qry.get_source() is not None)
    try:
        result, error = await qry.query()
        pytest.assume(not error)
        pytest.assume(len(result) > 0)
        print(f'{slug} count: {len(result)}')
    except Exception as err:
        raise Exception(err)
    finally:
        print("Generated in: %s" % (datetime.now() - startTime))
        await qry.close()
        return result

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(default_exception_handler)
    yield loop
    loop.close()


@pytest.fixture
async def connection(event_loop):
    qry = QueryConnection()
    await qry.start()
    yield qry
    await qry.close()

pytestmark = pytest.mark.asyncio

async def test_icims(event_loop, connection):
    """ Test icims jobs"""

    customer_id = 5674
    portal_id = 102
    subscription_id = 'b62922f4863646c1bd7a7904cfbdee91'
    legacy_url = 'https://ws-nav-api.mobileinsight.com/'

    tasks = [
        myquery(
            qc=connection,
            ev=event_loop,
            slug='jobs',
            conditions={
            "legacy": True,
            "type": "jobs",
            "customer_id" : customer_id,
            "url" : legacy_url,
            "portal_id" : portal_id,
            "test" : True
        }),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='people',
            conditions={
            "legacy": True,
            "type": "people",
            "customer_id" : customer_id,
            "url" : legacy_url,
            "portal_id" : portal_id,
            "test" : True
        }),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='stream_data',
            conditions={
            "type": "stream_data",
            "customer_id" : customer_id,
            "subscription_id" : subscription_id,
            "test" : True
        }),
    ]
    pytest.assume(len(tasks) > 0)
    results = await asyncio.gather(*tasks)
    for result in results:
        pytest.assume(result is not None)
