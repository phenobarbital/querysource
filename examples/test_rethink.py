# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
Test RethinkDB resources.
"""
import asyncio
from datetime import datetime
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)


conditions = {
    "fields": [
        "name", "fdate", "state_code", "address", "populartimes", "place_id"
    ],
    "date": ["2020-01-01", "2020-12-31"],
    "where_cond": {"city_id": [34480]},
    "refresh": True,
    "coldef": {
        "date": "date"
    }
}

async def query():
    startTime = datetime.now()
    try:
        qry = QuerySource(
            slug='troc_traffic',
            conditions=conditions,
            loop=loop
        )
        await qry.get_query()
        result, error = await qry.query()
        for row in result:
            print(row)
        await qry.close()
        print("Generated in: %s" % (datetime.now() - startTime))
    except Exception as err:
        print(err)
    finally:
        print("COMPLETED! ========")

if __name__ == '__main__':
    try:
        asyncio.run(query())
    finally:
        loop.stop()
