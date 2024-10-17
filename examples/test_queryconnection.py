"""Test QueryConnection."""
import asyncio
import uvloop
from datetime import datetime
from querysource.connections import QueryConnection
from querysource import QuerySource

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

qry = QueryConnection(loop=loop)
# start method: loading all datasources and create the default ones.
loop.run_until_complete(qry.start())


async def test_query():
    p = qry.pool()
    # using a connection in "datasources" table.
    query = QuerySource(slug='sample_airports')
    await query.get_query()
    result, error = await query.query()
    print(result)
    await query.close()

    # using the default connection for the Provider: sqlserver
    query = QuerySource(slug='sample_airports_default')
    await query.get_query()
    result, error = await query.query()
    print(result)
    await query.close()

    # query = QuerySource(slug='trendmicro_stores', conditions={"refresh": True})
    # await query.get_query()
    # result = await query.query()
    # # print(result)
    # # await query.close()
    #
    # query = QuerySource(slug='loreal_stores', conditions={"refresh": True})
    # await query.get_query()
    # result = await query.query()
    #
    # query = QuerySource(slug='epson_stores', conditions={"refresh": True})
    # await query.get_query()
    # result = await query.query()


if __name__ == '__main__':
    try:
        loop.run_until_complete(qry.start())
        q = QueryConnection(loop=loop)
        print('IS CONNECTED> ', q.connected)
        loop.run_until_complete( test_query() )
        loop.run_forever()
    except KeyboardInterrupt:
        print('EXIT FROM APP =========')
        loop.run_until_complete(qry.close())
