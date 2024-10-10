"""
Test.
"""
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import asyncio
from datetime import datetime
import timeit

from asyncdb import AsyncDB, AsyncPool
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource, QueryConnection

from navconfig.conf import asyncpg_url, DEBUG, database_url, QUERYSET_REDIS

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)

qry = QueryConnection()
loop.run_until_complete(qry.connect())

redis = loop.run_until_complete(qry.redis())
pool = qry.pool()

print('Is REDIS connected?: ', redis.is_connected())

conditions = {
    #"filterdate": "2020-10-07",
    "filter": {"region_name": "Dahl Evans"},
    "refresh": False
}

try:
    startTime = datetime.now()
    connection = loop.run_until_complete(pool.acquire())
    qry = QuerySource(
        slug='walmart_stores_with_goals',
        conditions=conditions,
        connection=connection,
        loop=loop,
        redis=redis
    )
    loop.run_until_complete(qry.get_query())
    result, error = loop.run_until_complete(qry.query())
    print('RESULT: ', len(list(result)))
    for row in result:
        print(row)
        #pass
    qry.terminate()
    print("Generated in: %s" % (datetime.now() - startTime))
finally:
     print("COMPLETED! ========")
     loop.run_until_complete(redis.close())
     loop.run_until_complete(pool.wait_close(gracefully=True, timeout=10))
     loop.stop()
