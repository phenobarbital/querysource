"""Tests.

Testing QuerySource.
"""

from datetime import datetime
import timeit
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import aiohttp
import pytest
from asyncdb.exceptions import default_exception_handler
from querysource.utils import cPrint

DRIVER = 'postgres'
DSN = "postgres://troc_pgdata:12345678@127.0.0.1:5432/navigator_dev"
params = {
    "host": '127.0.0.1',
    "port": '5432',
    "user": 'troc_pgdata',
    "password": '12345678',
    "database": 'navigator_dev'
}

TOKEN=""
API_HOST="http://nav-api.dev.local:5000/api/v2/services/queries/{slug}?refresh=1"
# WITH CACHE:
# API_HOST="http://navigator-dev.dev.local:5000/api/v2/services/queries/{slug}"
# DEV
# API_HOST="https://api-dev.navi.mobileinsight.com/api/v2/services/queries/{slug}"
#
# Testing cache:
# API_HOST="http://navigator-dev.dev.local:5000/api/v2/services/queries/{slug}"
# STAGING
# API_HOST="https://api-staging.navi.mobileinsight.com/api/v2/services/queries/{slug}"
# PROD:
# API_HOST = "https://nav-api.mobileinsight.com/api/v2/services/queries/{slug}?refresh=1"


async def query_api(query, evt):
    slug, conditions = query
    cPrint(
        f':: Running {slug}',
        level='INFO'
    )
    started = datetime.now()
    timeout = aiohttp.ClientTimeout(total=360)
    url = API_HOST.format(slug=slug)
    result = []
    status = None
    resp = None
    headers = {
        "Authorization": f"Bearer {TOKEN}"
    }
    try:
        async with aiohttp.ClientSession(json_serialize=json.dumps) as session:
            async with session.post(url,timeout=timeout, json=conditions, headers=headers) as response:
                status = response.status
                print('HTTP STATUS: ', status)
                if status not in (200, 204, 404):
                    resp = await response.text(encoding='utf-8')
                # check if result, empty or not found
                pytest.assume(response.status in (200, 204, 404))
                if status == 200:
                    pytest.assume(result is not None)
                    result = await response.json()
                return True
    except Exception as err:
        pytest.fail(
            str(err)
        )
    finally:
        print(f'{slug}: {status} - {resp}')
        print(f'{slug} count: {len(result)}')
        ended = datetime.now()
        generated_at = (ended - started).total_seconds()
        print(f"Generated in: {generated_at}")

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
        ('epson_training_request_by_modality', {"lastdate": "2021-07-26", "firstdate": "2021-07-01", "where_cond": {}}),
        ('trendmicro_field_activity', {"firstdate": "2021-05-30", "lastdate": "2021-07-03"}),
        ('walmart_zero_sales_mtd', {"fields": ["count(store_id) as num_zero_stores"], "firstdate": "2021-06-01", "grouping": ["company_id"],  "lastdate": "2021-06-30", "where_cond": {"region_name": "Thomas Gray"}}),
        ('walmart_prepaid_employee_by_day', {"refresh": True}),
        ('walmart_accessories_employee_by_day', {"refresh": True}),
        ('walmart_wearables_employee', {"fields": ["operator_name", "quantity"], "filterdate": "2021-06-30"}),
        ('troc_oportunities_won', {}),
        ('troc_oportunities_lost', {}),
        # ('troc_traffic', {}),
        # ('troc_places_details', {"place_id": "ChIJN68h5T8ruIcRMaRNdl44cvU"}),
        ('walmart_stores', {"refresh": True}),
        ('flexroc_stores', {"refresh": True}),
        ('epson_stores', {}),
        ('epson_store_types', {}),
        ('loreal_stores', {}),
        ('troc_uap_employees', {"pagesize": 100, "more_results": False}),
        ('cricket_completed_visits', {}),
        ('cricket_stores', {}),
        ('cricket_store_types', {}),
        ('walmart_postpaid_sales_by_day', {"lastdate":"2021-07-10","firstdate":"2021-07-10","where_cond":{}}),
        ('epson_visits_goal', {"fields":["visits"," Round((visit_to_goal * 100), 1) as visit_to_goal","(visit_goal*active_dsm) as visit_goal","visit_goal as visit_weeks_goal","active_dsm"],"lastdate":"2021-07-03","firstdate":"2021-06-27","where_cond":{}}),
        ('epson_visits_to_goal',{"fields":["description","round(visit_to_goal * 100, 2) as visit_to_goal"],"lastdate":"2021-07-03","firstdate":"2021-06-27","hierarchy":["region_id","market_id"],"where_cond":{},"qry_options":{"select_child":"true"}}),
        ('epson_field_activity', {"lastdate":"2021-07-28","firstdate":"2021-07-01","where_cond":{}}),
        ('trendmicro_activity', {"lastdate":"2021-07-28","firstdate":"2021-07-01","where_cond":{}}),
        ('flexroc_field_activity', {"lastdate":"2021-06-19","firstdate":"2021-06-13","where_cond":{}}),
        ('flexroc_compliance_chart', {"lastdate":"2021-06-19","firstdate":"2021-06-13","where_cond":{}}),
        ('epson_engagement', {"lastdate":"2021-07-28","firstdate":"2021-07-01","where_cond":{}}),
        ('trendmicro_stores', {}),
        ('walmart_postpaid_yoy_by_day', {"fields":["activated_date","num_sales","past_sales","round(yoy_postpaid_units_trend * 100, 2) as yoy_postpaid_units_trend"],"filterdate":"2021-07-10","where_cond":{"territory_id":"null"},"qry_options":{"null_rolldown":"true"}}),
        ('playstation_projects_definition', {}),
        ('playstation_activity_cdr', {
            "fields": [
                "sum(total_calls) as total_calls",
                "(sum(answered_calls) / sum(total_calls)) as answer_rate",
                "(sum(total_time_to_answer_sec) / sum(total_calls)) as avg_time_to_answer",
                "troc_percent(sum(rejected_calls), sum(total_calls)) as declined_rate",
                "troc_percent(sum(unanswered_calls), sum(total_calls)) as unanswered_rate",
                "troc_percent(sum(busy_call)::numeric, sum(distinct_calls)::numeric) as busy_rate",
                "troc_percent(sum(customer_cancel_calls), sum(total_calls)) as abandonment_rate",
                "troc_percent(sum(test_call), sum(total_calls)) as test_calls_rate",
                "to_char((coalesce(sum(call_duration_sec)/sum(total_calls),0) || ' second')::interval, 'MI:SS') as avg_call_duration",
                "troc_percent(sum(direct_call) , sum(total_calls)) as direct_to_agent",
                "troc_percent(sum(valerie_call), sum(total_calls)) as transfer_from_valerie"
            ],
            "lastdate": "2021-07-31",
            "firstdate": "2021-07-01"
        }),
        ('playstation_gb42_activity_ps', {
            "fields":["sum(total_calls) as calls",
                      "unnest(product_name) as product_name"],
            "grouping":["unnest(product_name)"],
            "lastdate":"2021-08-30",
            "firstdate":"2021-08-01",
            "where_cond":{"product_name|":
                ["PS Colombia Dprimero","Accesorios","Video Juegos"]}
        }),
        (
            'playstation_gb42_microsite_events', {
                "fields": ["sum(total_visits) as total_visits","sum(total_users) as total_users","sum(total_returning_users) as total_returning_users","coalesce(sum(page_views)/sum(total_visits),0) as avg_page_views","coalesce(sum(content_views)/sum(total_visits),0) as avg_content_views","coalesce(sum(downloads)/sum(total_visits),0) as avg_downloads","to_char((coalesce(ceil(sum(total_duration)/sum(total_visits)),0) || ' second')::interval, 'MI:SS') as avg_session_duration","to_char((coalesce(ceil(sum(agent_duration)/nullif(sum(agent_calls),0)),0) || ' second')::interval, 'MI:SS') as avg_agent_duration"],
                "lastdate":"2021-08-30",
                "firstdate":"2021-08-01",
                "where_cond":{}
            }
         ),
        (
            'teamdirect_microsite_events', {
                "fields": [
                    "sum(total_visits) as total_visits",
                    "sum(total_users) as total_users",
                    "sum(total_returning_users) as total_returning_users",
                    "coalesce(sum(page_views)/sum(total_visits),0) as avg_page_views",
                    "coalesce(sum(content_views)/sum(total_visits),0) as avg_content_views",
                    "coalesce(sum(downloads)/sum(total_visits),0) as avg_downloads",
                    "to_char((coalesce(ceil(sum(total_duration)/sum(total_visits)),0) || ' second')::interval, 'MI:SS') as avg_session_duration","to_char((coalesce(ceil(sum(agent_duration)/nullif(sum(agent_calls),0)),0) || ' second')::interval, 'MI:SS') as avg_agent_duration"],
                "lastdate":"2021-09-15",
                "firstdate":"2021-09-01",
                "where_cond":{}
            }
        ),
        (
            'epson_training_requested_delivered', {
                "lastdate": "2023-01-31",
                "firstdate": "2023-01-01"
            }
        ),
        (
            'epson_training_delivered_by_modality', {
                "lastdate": "2023-01-31",
                "firstdate": "2023-01-01"
            }
        ),
        (
            'epson_engagement', {
                "lastdate": "2023-01-31",
                "firstdate": "2023-01-01"
            }
        ),
        (
            'epson_revenue_vs_activity_ecotank', {
                "lastdate": "2023-01-31",
                "firstdate": "2023-01-01"
            }
        ),
        (
            'epson_field_activity', {
                "lastdate": "2023-01-31",
                "firstdate": "2023-01-01"
            }
        ),
        (
            'epson_visits_to_goal', {
                "fields": [
                    "description",
                    "round(visit_to_goal * 100, 2) as visit_to_goal"
                ],
                "lastdate": "2022-08-18",
                "firstdate": "2022-08-13",
                "hierarchy": [
                    "region_id",
                    "market_id"
                ],
                "qry_options": {
                    "select_child": "true"
                }
            }
        )
    ]
    with ThreadPoolExecutor() as ex:
        args = ((s, event_loop) for s in slugs)
        for obj in ex.map(lambda p: query_api(*p), args):
            result = await obj
            pytest.assume(result is not None)
