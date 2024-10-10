"""Tests.

Testing QuerySource.
"""
import asyncio
from datetime import datetime
import pytest
from asyncdb.drivers.base import BaseDriver
from asyncdb.exceptions import default_exception_handler
from navigator import Application
from querysource.queries.qs import QS
from querysource.connections import QueryConnection

q = """SELECT query_slug, conditions
 from public.queries where program_slug= 'walmart'
 AND provider = 'db' AND dwh = FALSE"""

async def myquery(evt: asyncio.AbstractEventLoop, slug: str = '', conditions: dict = None):
    print(f':: Running {slug}')
    started = datetime.now()
    query = QS(
        slug=slug,
        conditions=conditions,
        output_format='pandas',
        loop=evt
    )
    pytest.assume(query is not None)
    result, _ = await query.dry_run()
    print(' === Execute Query === ')
    try:
        result, error = await query.query()
        ended = datetime.now()
        print(f'{slug} count: {len(result)}')
        pytest.assume(error is None)
        generated_at = (ended - started).total_seconds()
        print('Parsing Time: ', generated_at)
        return True
    except Exception as err:
        ended = datetime.now()
        generated_at = (ended - started).total_seconds()
        pytest.fail(str(err))
    finally:
        print(f"Generated in: {generated_at}")
        await query.close()

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_exception_handler(default_exception_handler)
    yield loop
    loop.close()

pytestmark = pytest.mark.asyncio

queries_test = [
    {
        "query_slug": "walmart_stores",
        "conditions": {
            "fields": "store_id, store_name, region_id, region_name, district_id, district_name",
            "querylimit": 100,
            "group_by": ["store_id", "store_name", "region_id", "region_name", "district_id", "district_name"]
        }
    },
    {
        "query_slug": 'epson_stores',
        "conditions": {
            "refresh": 1
        }
    },
    {
        "query_slug": 'flexroc_stores',
        "conditions": {
            "refresh": 1
        }
    },
    # {
    #     "query_slug": 'epson_form_data',
    #     "conditions": {
    #         "refresh": 1,
    #         "formid": 2662,
    #         "startdate": "2022-08-07T00:00:00",
    #         "enddate": "2022-08-08T00:00:00"
    #     }
    # },
    {
        "query_slug": 'troc_uap_employees',
        "conditions": {
            "pagesize": 100,
            "more_results": False
        }
    },
    # {
    #     "query_slug": "trendmicro_stores_definition",
    #     "conditions": {
    #         "accountName": "Best Buy",
    #         "pageSize": 200,
    #         "orgid": 77
    #     }
    # },
    # {
    #     "query_slug": 'flexroc_form_data',
    #     "conditions": {
    #         "orgid": 74,
    #         "formid": 2972,
    #         "startdate": "2022-07-19T00:00:00",
    #         "enddate": "2022-07-20T00:00:00",
    #     }
    # },
    {
        "query_slug": "walmart_mtd_prepaid_to_goal",
        "conditions": {
            "fields": [
                "description",
                "prepaid_sales",
                "prepaid_to_goal"
            ],
            "ordering": [
                "prepaid_to_goal DESC"
            ],
            "filterdate": "2022-09-30",
            "where_cond": {
                "territory_id": "!null"
            },
            "qry_options": {
                "select_child": "true"
            }
        }
    }
]
async def test_slugs(event_loop):
    """ Test Query Slugs"""
    qry = QueryConnection(loop=event_loop, lazy=True)
    # define a new Application
    app = Application()
    await qry.start(app)
    pytest.assume(qry.is_connected() is True)
    db = qry.get_connection()
    pytest.assume(isinstance(db, BaseDriver))
    async with await db.connection() as conn:
        pytest.assume(conn.is_connected() is True)
        queries, error = await conn.query(q)
        pytest.assume(len(queries) > 0)
        pytest.assume(not error)
        for i in range(5):
            tasks = []
            for query in queries_test:
                slug = query['query_slug']
                conditions = query['conditions']
                tasks.append(
                    myquery(
                        event_loop,
                        slug=slug,
                        conditions=conditions
                    )
                )
            pytest.assume(len(tasks) > 0)
            results = await asyncio.gather(*tasks)
            for result in results:
                pytest.assume(result is True)
        await asyncio.sleep(2.0)
    await qry.stop()
