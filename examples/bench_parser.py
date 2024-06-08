"""
Testing Parser.
"""
import asyncio
from querysource.connections import QueryConnection
from querysource.models import QueryModel, QueryObject
from querysource.parsers.sql import SQLParser
import time


async def get_query(db):
    async with await db.connection() as conn:
        QueryModel.Meta.connection = conn
        qry = await QueryModel.get(query_slug='walmart_all_apd')
    conditions = {
        "fields": [
            "postpaid_apd as all_stores",
            "postpaid_apd_7p as seven_plus",
            "postpaid_apd_legacy as legacy_2016",
            "postpaid_apd_expansion as expansion_2017",
            "postpaid_apd_expansion2018 as expansion_2018",
            "postpaid_apd_expansion2019 as expansion_2019",
            "postpaid_apd_expansion2021 as expansion_2021"
        ],
        "filterdate": "2022-10-15",
        "querylimit": 10,
        "where_cond": {
            "territory_id": "null"
        },
        "qry_options": {
            "null_rolldown": "true"
        }
    }
    b = QueryObject(**conditions)
    parser = SQLParser(
        definition=qry,
        conditions=b
    )
    await parser.set_options()
    b = await parser.build_query()
    return b


async def main():
    conn = QueryConnection(lazy=True)
    db = await conn.get_connection()
    sql = await get_query(db)
    print(sql)

if __name__ == "__main__":
    start_time = time.time()
    measures = []
    size = 1000
    for _ in range(size):
        _start = time.time()
        asyncio.run(main())
        _end = time.time()
        measures.append(_end - _start)
    end_time = time.time()

    total_time = end_time - start_time
    print(f"Total execution time: {total_time} seconds")
    print(f"Average execution time: {total_time / size} seconds")
    print(f'Sample: {measures[0]} seconds')
