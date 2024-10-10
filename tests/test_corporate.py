"""Tests.

Testing QuerySource.
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
    if not conditions:
        conditions = {
            "firstdate": "2019-01-01",
            "lastdate": "2019-01-31",
            "refresh": True
        }
    startTime = datetime.now()
    print(timeit.timeit('1 + 3 ', number=50000000))
    # connection = await qc.get_connection()
    # pytest.assume(connection.is_connected() is True)
    qry = QuerySource(
        slug=slug,
        conditions=conditions,
        # connection=connection,
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


async def test_slugs(event_loop, connection):
    """ Test Query Slugs"""
    tasks = [
        myquery(
            qc=connection,
            ev=event_loop,
            slug='corporate_dashboard_adp',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='turnover_corp_dashboard',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='xfinity_transaction_sales',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='samsung_kpi',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='wm_reset_Corp_Dash_Hours',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='wm_assembly_Corp_Dash_Hours',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='wm_assembly_salaries',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='wm_reset_billable_expenses',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_corporate_revenue_sold',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_corporate_revenue_billed',
            conditions={"refresh": True}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_leads_report',
            conditions={"refresh": True}
        ),
        # myquery(
        #     qc=connection,
        #     ev=event_loop,
        #     slug='troc_oportunities_lost',
        #     conditions={"refresh": True}
        # ),
        # myquery(
        #     qc=connection,
        #     ev=event_loop,
        #     slug='troc_oportunities_won',
        #     conditions={"refresh": True}
        # ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_pipeline_by_industry',
            conditions={"refresh": True}
        )
    ]
    pytest.assume(len(tasks) > 0)
    results = await asyncio.gather(*tasks)
    for result in results:
        pytest.assume(result is True)
