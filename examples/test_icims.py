"""
Test icims resources.
"""
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import asyncio
from datetime import datetime
import timeit

from asyncdb import AsyncDB, AsyncPool
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource

from navconfig.conf import asyncpg_url, DEBUG, database_url, QUERYSET_REDIS

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)

# asynpg pool
pool = AsyncPool('pg', dsn=asyncpg_url, loop=loop)
loop.run_until_complete(pool.connect())


redis = AsyncDB('redis', dsn=QUERYSET_REDIS, loop=loop)
loop.run_until_complete(redis.connection())
print('Is REDIS connected?: ', redis.is_connected())

customer_id = 5674
portal_id = 102
subscription_id = 'b62922f4863646c1bd7a7904cfbdee91'
legacy_url = 'https://ws-nav-api.mobileinsight.com/'

async def myquery(slug: str, conditions: dict = None):
    driver = {'driver': 'rest', 'source': 'icims', 'method': slug}
    row = []
    try:
        startTime = datetime.now()
        connection = await pool.acquire()
        qry = QuerySource(
            driver=driver,
            query_raw=None,
            conditions=conditions,
            connection=connection,
            loop=loop,
            redis=redis
        )

        await qry.get_query()
        result, error = await qry.query()
        for row in result:
            print(row)
            #pass
        await qry.close()
        print("Generated in: %s" % (datetime.now() - startTime))
        
    finally:
        print("Generated in: %s" % (datetime.now() - startTime))
        return True

try:
    tasks = [
        myquery(slug='jobs', conditions={
            "legacy": True,
            "type": "jobs",
            "customer_id" : customer_id,
            "url" : legacy_url,
            "portal_id" : portal_id,
            "test" : True
        }),
        myquery(slug='people', conditions={
            "legacy": True,
            "type": "people",
            "customer_id" : customer_id,
            "url" : legacy_url,
            "portal_id" : portal_id,
            "test" : True
        }),
        myquery(slug='stream_data', conditions={
            "type": "stream_data",
            "customer_id" : customer_id,
            "subscription_id" : subscription_id,
            "test" : True
        }),
    ]
    loop.run_until_complete(asyncio.gather(*tasks, loop=loop))

finally:
    print("COMPLETED! ========")
    loop.run_until_complete(redis.close())
    loop.run_until_complete(pool.wait_close(gracefully=True, timeout=10))


