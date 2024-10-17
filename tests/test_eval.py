"""Tests.

Testing QuerySource.
"""
import logging
import pytest
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

pytestmark = pytest.mark.asyncio

async def myquery(qc, ev, slug='', conditions: dict = None):
    cPrint(
        f':: Running {slug}',
        level='INFO')
    if not conditions:
        conditions = {
            "firstdate": "2019-01-01",
            "lastdate": "2019-01-31",
            "refresh": True
        }
    startTime = datetime.now()
    print(timeit.timeit('1 + 3 ', number=50000000))
    # redis = await qc.redis()
    connection = await qc.get_connection()
    pytest.assume(connection.is_connected() is True)
    qry = QuerySource(
        slug=slug,
        conditions=conditions,
        connection=connection,
        loop=ev
    )
    pytest.assume(qry is not None)
    try:
        await qry.get_query()
    except Exception as err:
        pytest.fail(err)
    pytest.assume(qry.get_source() is not None)
    result = None
    try:
        result, error = await qry.query()
        if error:
            logging.debug(f"Slug: {slug}, error: {error}")
        pytest.assume(not error)
        pytest.assume(result is not None)
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

slugs = [
         ("flexroc_stores", {"refresh": 1}),
         ("loreal_stores", {"refresh": 1}),
         ("walmart_stores", {"refresh": 1})
        ]
@pytest.mark.parametrize("slug, conditions", slugs)
async def test_slug(event_loop, connection, slug, conditions):
    cPrint(f':: Running {slug}', level='INFO')
    try:
        result = await myquery(
            qc=connection,
            ev=event_loop,
            slug=slug,
            conditions=conditions
        )
        await asyncio.sleep(2, loop=event_loop)
        pytest.assume(result is not None)
    except Exception as err:
        cPrint(f'Error running {slug}', level='ERROR')
        # raise Exception(str(err))
