# -*- coding: utf-8 -*-
#!/usr/bin/env python3

import os
import sys

import asyncio
from datetime import datetime
import timeit

from asyncdb import AsyncDB, AsyncPool
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource, QueryConnection
from navconfig.conf import asyncpg_url, DEBUG, database_url, QUERYSET_REDIS
# migrate to asyncDB
from asyncdb.providers.redis import redis

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)

async def start_connection():
    qry = QueryConnection()
    await qry.connect()
    return qry

async def close_connection(qc):
    await qc.close()

query = "SELECT {fields} FROM walmart.operational_scorecard({filterdate}) {where_cond}"
query_raw = 'SELECT * FROM walmart.operational_scorecard(current_date)'

conditions = {
    "fields": [
        "description", "division", "region", "market", "store_id",
        "store_name", "fte", "fte_to_goal", "total_payroll_minutes",
        "trended_to_budget", "overtime_hours", "peak_hours_coverage",
        "minutes_missed", "violation_fees", "violation_per_hour",
        "dark_days", "dark_days_percent", "visit_count_mtd",
        "visit_time", "avg_duration_per_visit", "visit_to_goal",
        "no_visits", "training_certification", "turnover", "placeid"
    ],
    "filterdate": "2019-02-25",
    "where_cond": {"region": "Dahl Evans"},
    "refresh": True,
    "dwh": True
}

cond_definition = {
    "filterdate": "date"
}


async def myquery(conn, slug='', conditions: dict = None):
    row = []
    if not conditions:
        conditions = {
            "firstdate": "2019-01-01",
            "lastdate": "2019-01-31",
            "refresh": True
        }
    try:
        startTime = datetime.now()
        print(timeit.timeit('1 + 3 ', number=50000000))
        connection = await pool.acquire()
        qry = QuerySource(slug=slug, conditions=conditions, connection=connection, loop=loop, redis=redis)
        await qry.get_query()
        result = await qry.query()
        for row in result:
            print(row)
        await qry.close()
    finally:
        print("Generated in: %s" % (datetime.now() - startTime))
        return True

# # using QuerySource
try:
    conditions = {}
    qc = loop.run_until_complete(start_connection())
    redis = qc.redis()
    connection = loop.run_until_complete(qc.get_connection())
    qry = QuerySource(slug='walmart_stores', options={}, connection=connection, loop=loop, redis=redis)
    result = loop.run_until_complete(qry.query())
    for row in result:
        print(row)
    qry.terminate()

    conditions = {
        "place_id": "ChIJYe-sUUPV2IgR13HMVPtyDso",
        "refresh": "1",
    }
    print('---- Provider: rest, dialect: google')
    qry = QuerySource(slug='troc_places_details', options=conditions, connection=connection, loop=loop, redis=redis)
    result = loop.run_until_complete(qry.query())
    for row in result:
        print(row)
    qry.terminate()
    #
    print('---- Provider: rest, dialect: get_populartimes')
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
    qry = QuerySource(slug='troc_traffic', options=store, connection=connection, loop=loop, redis=redis)
    result = loop.run_until_complete(qry.query())
    for row in result:
        print(row)
    qry.terminate()
    #
    # #make a task list for asyncio tasks
    tasks = [
        myquery(slug='corporate_dashboard_adp'),
        myquery(slug='apple_sales_uncovered'),
        myquery(slug='apple_sales_total_visits'),
        myquery(slug='apple_focus_breakdown_by_carrier'),
        myquery(slug='apple_photos'),
        myquery(slug='apple_focus_breakdown'),
        myquery(slug='samsung_locations', conditions={}),
        myquery(slug='troc_uap_employees', conditions={"pagesize": 10, "more_results": False}),
    ]
    loop.run_until_complete(asyncio.gather(*tasks))

finally:
     print("COMPLETED! ========")
     loop.run_until_complete(close_connection(qc))
     loop.stop()
