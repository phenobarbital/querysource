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
    async with await db.connection() as conn:
        QueryModel.Meta.connection = conn
        troc = await QueryModel.get(query_slug='troc_files')
    conditions = {
        "fields": "file_uid, file_slug, mimetype, filename",
        "program_id": 3,
        "filter_by": {
            "task_enabled": True,
            "active": True
        },
        "order_by": ["program_id"],
        "querylimit": 10
    }
    b = QueryObject(**conditions)
    parser = SQLParser(
        query="SELECT {fields} FROM troc.files {filter}",
        definition=troc,
        conditions=b
    )
    await parser.set_options()
    b = await parser.build_query()
    print('SQL:: ', b)


asyncio.run(model())
