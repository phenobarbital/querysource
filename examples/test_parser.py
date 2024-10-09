"""
Testing Parser.
"""
import asyncio
from querysource.connections import QueryConnection
from querysource.models import QueryModel, QueryObject
from querysource.parsers.sql import SQLParser


async def model():
    conn = QueryConnection(lazy=True)
    db = await conn.get_connection()
    # async with await db.connection() as conn:
    #     QueryModel.Meta.connection = conn
    #     troc = await QueryModel.get(query_slug='troc_files')
    # conditions = {
    #     "fields": "file_uid, file_slug, mimetype, filename",
    #     "program_id": 3,
    #     "filter_by": {
    #         "task_enabled": True,
    #         "active": True
    #     },
    #     "order_by": ["program_id"],
    #     "querylimit": 10
    # }
    # b = QueryObject(**conditions)
    # parser = SQLParser(
    #     query="SELECT {fields} FROM troc.files {filter}",
    #     definition=troc,
    #     conditions=b
    # )
    # await parser.set_options()
    # b = await parser.build_query()
    # print('SQL:: ', b)
    async with await db.connection() as conn:
        QueryModel.Meta.connection = conn
        qry = await QueryModel.get(query_slug='walmart_all_apd')
    print(
        f'QUERY > {qry}'
    )
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
    print('SQL:: ', b)


asyncio.run(model())
