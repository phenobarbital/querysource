"""Tests.

Testing QuerySource.
"""
import pytest
from asyncdb import AsyncDB, AsyncPool
import asyncio
from datetime import datetime
import timeit

from asyncdb.exceptions import default_exception_handler
from concurrent.futures import ThreadPoolExecutor
from querysource import QuerySource
from querysource.connections import QueryConnection
from querysource.utils.functions import cPrint
from functools import partial
from itertools import repeat

DRIVER = 'postgres'
DSN = "postgres://troc_pgdata:12345678@127.0.0.1:5432/navigator_dev"
params = {
    "host": '127.0.0.1',
    "port": '5432',
    "user": 'troc_pgdata',
    "password": '12345678',
    "database": 'navigator_dev'
}


async def myquery(slug, qc, ev):
    cPrint(
        f':: Running {slug}',
        level='INFO'
    )
    conditions = {
        "refresh": True
    }
    startTime = datetime.now()
    print(timeit.timeit('1 + 3 ', number=50000000))
    connection = await qc.get_connection()
    pytest.assume(connection.is_connected() is True)
    qry = QuerySource(
        slug=slug,
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
        pytest.assume(result is not None)
        print(f'{slug} count: {len(result)}')
    except Exception as err:
        raise Exception(err)
    finally:
        print("Generated in: %s" % (datetime.now() - startTime))
        await qry.close()
        return True

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

async def test_threads(event_loop, connection):
    slugs = [
        'walmart_stores',
        'loreal_stores',
        'epson_stores',
        'flexroc_stores'
    ]
    with ThreadPoolExecutor() as ex:
        args = ((s, connection, event_loop) for s in slugs)
        for obj in ex.map(lambda p: myquery(*p), args):
            result = await obj
            pytest.assume(result is not None)
