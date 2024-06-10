"""Tests.

Testing QuerySource.
"""
import sys
import logging
import pytest
import asyncio
from datetime import datetime
import timeit
from querysource.services import QuerySource
from querysource.connections import QueryConnection
from querysource.utils.functions import cPrint
import pytest_asyncio

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

async def myquery(qc, ev, slug, conditions: dict = None):
    cPrint(
        f':: Running {slug}',
        level='DEBUG'
    )
    if not conditions:
        conditions = {
            "firstdate": "2019-01-01",
            "lastdate": "2019-01-31",
            "refresh": True
        }
    startTime = datetime.now()
    print(timeit.timeit('1 + 3 ', number=50000000))
    connection = await qc.get_connection()
    pytest.assume(connection.is_connected() is True)
    qry = QuerySource(
        slug=slug,
        conditions=conditions,
        lazy=True,
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
        if error or not result:
            logging.debug(f"Slug: {slug}, error: {error}")
        if result:
            cPrint(
                f':: Running {slug}',
                level='INFO'
            )
        else:
            cPrint(
                f':: Running {slug}',
                level='WARN'
            )
        pytest.assume(not error)
        pytest.assume(result is not None)
        print(f'{slug} count: {len(result)}')
    except Exception as err:
        raise Exception(err)
    finally:
        print("Generated in: %s" % (datetime.now() - startTime))
        await qry.close()
        return [slug, result]


@pytest.fixture
async def connection(event_loop):
    qry = QueryConnection(lazy=True, loop=event_loop)
    await qry.start()
    yield qry
    await qry.close()

pytestmark = pytest.mark.asyncio

async def test_slugs(event_loop, connection):
    """ Test Query Slugs"""
    places_details = {
        "place_id": "ChIJYe-sUUPV2IgR13HMVPtyDso",
        "refresh": "1",
    }
    store = {
        "fields": [
            "name",
            "fdate",
            "state_code",
            "address",
            "populartimes",
            "place_id",
            "visits"
        ],
        "filterdate": [
            "2020-05-01",
            "2020-05-30"
        ],
        "where_cond": {
            "company_id": 10,
            "state_code": "FL"
        }
    }
    tasks = [
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_uap_employees',
            conditions={"pagesize": 100, "more_results": False}
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_stores',
            conditions={
                "refresh": 1
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_places_details',
            conditions=places_details
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='troc_traffic',
            conditions=store
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='epson_stores',
            conditions={
                "refresh": 1
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='epson_success_index',
            conditions={
                "lastdate":"2021-07-03",
                "firstdate":"2021-06-27",
                "where_cond":{}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='playstation_store_types_def',
            conditions={
                "refresh": False
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='epson_form_data',
            conditions={
                "refresh": 1,
                "formid": 2662,
                "startdate": "2021-08-07T00:00:00",
                "enddate": "2021-08-08T00:00:00",
                "dwh": True
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='flexroc_compliance_card',
            conditions={
                "lastdate":"2021-05-29",
                "firstdate":"2021-05-23",
                "where_cond":{}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_stores',
            conditions={
                "refresh": 1
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='flexroc_stores',
            conditions={
                "refresh": 1
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='loreal_stores',
            conditions={
                "refresh": 1
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_postpaid_sales_by_day',
            conditions={
                "lastdate":"2021-07-10","firstdate":"2021-07-10",
                "where_cond":{}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_sfs_metrics',
            conditions={
                "refresh": 1,
                "filterdate": "2021-07-10",
                "where_cond": {}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_mtd_postpaid_to_goal',
            conditions={
                "fields":["description","postpaid_sales","postpaid_to_goal"],"ordering":["postpaid_sales DESC"],
                "filterdate":"2021-06-29",
                "where_cond":{"territory_id":"!null"},
                "qry_options":{"select_child":"true"}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_kpi_metrics',
            conditions={
                "filterdate":"2021-06-29",
                "where_cond":{}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_all_apd',
            conditions={
                "fields":["postpaid_apd as all_stores","postpaid_apd_7p as seven_plus","postpaid_apd_legacy as legacy_2016","postpaid_apd_expansion as expansion_2017","postpaid_apd_expansion2018 as expansion_2018","postpaid_apd_expansion2019 as expansion_2019","postpaid_apd_expansion2021 as expansion_2021"],
                "filterdate":"2021-03-24",
                "where_cond":{"district_id": 153},
                "qry_options":{"null_rolldown":"true"}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_store_metrics',
            conditions={
                    "fields":["postpaid_sales","postpaid_to_goal","yoy_postpaid_units_trend as yoy_postpaid","prepaid_sales","prepaid_to_goal","accessories_sales","accessories_to_goal","wearables_units"],
                    "filterdate":"2021-03-24",
                    "where_cond":{"district_id": 153},
                    "qry_options":{"null_rolldown":"true"}
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='walmart_postpaid_store_ranking',
            conditions={
                "fields": [
                    "store_name",
                    "sales",
                    "rank() over (order by sales DESC NULLS LAST) as ranking"
                ],
                "filterdate": "2022-01-31",
                "querylimit": 10
            }
        ),
        myquery(
            qc=connection,
            ev=event_loop,
            slug='trendmicro_stores_definition',
            conditions={
                "accountName": "Best Buy",
                "pageSize": 100,
                "dwh": True
            }
        ),
    ]
    pytest.assume(len(tasks) > 0)
    results = await asyncio.gather(*tasks)
    for r in results:
        result = r[1]
        slug = r[0]
        print(f'TEST {slug}: ', (result is not None))
        if result is None:
            cPrint(
                f':: FAILED {slug}',
                level='WARN'
            )
        pytest.assume(result is not None)
        # await asyncio.sleep(1.0, loop=event_loop)

def pytest_sessionfinish(session, exitstatus):
    asyncio.get_event_loop().close()
