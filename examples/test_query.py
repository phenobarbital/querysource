"""
Test.
"""
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import asyncio
from datetime import datetime
import timeit
from querysource.utils.functions import cPrint
from asyncdb import AsyncDB, AsyncPool
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource
from querysource.connections import QueryConnection

from navconfig.conf import asyncpg_url, DEBUG, database_url, QUERYSET_REDIS

loop = asyncio.get_event_loop()
loop.set_exception_handler(default_exception_handler)


async def start():
    qry = QueryConnection(lazy=True)
    await qry.start()
    print('=================')
    return qry

async def stop(qry):
    await qry.close()


async def myquery(connection, conditions, slug):
    try:
        startTime = datetime.now()
        qry = QuerySource(
            slug=slug,
            conditions=conditions,
            connection=connection,
            loop=loop
        )
        await qry.get_query()
        result, error = await qry.query()
        cPrint(f'RESULT: {len(result)}')
        for row in result:
            pass
        print("Generated in: %s" % (datetime.now() - startTime))
    finally:
        await qry.close()

slugs = [
    # 'trendmicro_stores_definition',
    'walmart_postpaid_store_ranking'
]

conditions = {
    "accountName": "Best Buy",
    "pageSize": 100,
    "dwh": True
}

conditions = {
  "fields": [
    "store_name",
    "sales",
    "rank() over (order by sales DESC NULLS LAST) as ranking"
  ],
  "filterdate": "2022-01-31",
  "querylimit": 10
}

def main():
    try:
        qry = loop.run_until_complete(start())
        for slug in slugs:
            loop.run_until_complete(myquery(qry, conditions, slug))
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(stop(qry))
    finally:
        print('Closing All Connections ...')
        loop.stop()


if __name__ == '__main__':
    main()
