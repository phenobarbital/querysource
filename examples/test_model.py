"""Tests for Query model."""
import asyncio
from asyncdb.exceptions import default_exception_handler
from querysource.models import QueryUtil
from querysource import QueryConnection

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)

qry = QueryConnection()
loop.run_until_complete(qry.connect())

redis = qry.redis()
pool = qry.pool()
db = loop.run_until_complete(pool.acquire())

try:
    mdl = QueryUtil()
    #print(mdl.Meta.___dict__)
    mdl.Meta.set_connection(db)
finally:
    print("COMPLETED! ========")
    loop.run_until_complete(pool.wait_close(gracefully=True, timeout=10))
    loop.stop()
