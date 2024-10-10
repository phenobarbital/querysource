"""
Test REST resources.
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


conditions = {
  "refresh": 1,
  "formid": 2662,
  "startdate": "2020-12-07T00:00:00",
  "enddate": "2020-12-08T00:00:00"
}

cond_definition = {
    "startdate": "date",
    "enddate": "date",
    "formid": "integer"
}

try:
    startTime = datetime.now()
    connection = loop.run_until_complete(pool.acquire())
    qry = QuerySource(
        slug='epson_form_data',
        conditions=conditions,
        connection=connection,
        loop=loop,
        redis=redis
    )
    loop.run_until_complete(qry.get_query())
    result, error = loop.run_until_complete(qry.query())
    for row in result:
        print(row)
        #pass
    loop.run_until_complete(qry.close())
    print("Generated in: %s" % (datetime.now() - startTime))
finally:
     print("COMPLETED! ========")
     loop.run_until_complete(redis.close())
     loop.run_until_complete(pool.wait_close(gracefully=True, timeout=10))
     #loop.stop()
