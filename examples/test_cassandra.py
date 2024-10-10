"""
Test Cassandra resources.
"""
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import asyncio
from datetime import datetime
import timeit

from asyncdb import AsyncDB
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource

from navconfig.conf import asyncpg_url, DEBUG, database_url

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)

PARAMS = {
    "host": "127.0.0.1",
    "port": "9042",
    "username": 'cassandra',
    "password": 'cassandra',
    "database": 'library'
}

connection = AsyncDB('cassandra', params=PARAMS)

conditions = {
    "fields": [
        "name", "fdate", "state_code", "address", "populartimes", "place_id"
    ],
    "filterdate": ["2020-09-01", "2020-12-07"],
    "where_cond": {"city_id": [34480]}
}

async def query():
    startTime = datetime.now()
    try:
        await connection.connection()
        print('IS Cassandra Connected: {}'.format(connection.is_connected()))
        connection.use('library')
        await connection.execute('PAGING 1000')
        qry = QuerySource(
            query_raw='SELECT * FROM library.events LIMIT 1000',
            conditions=conditions,
            connection=connection,
            loop=loop,
            driver={
              "driver": 'cassandra',
              "datasource": PARAMS
            }
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
