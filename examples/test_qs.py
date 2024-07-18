import asyncio
from datetime import datetime
from querysource.queries.qs import QS


async def start():
    started = datetime.now()
    conditions = {
        "fields": "store_id, store_name, region_id, region_name, district_id, district_name",
        "querylimit": 100,
        "group_by": ["store_id", "store_name", "region_id", "region_name", "district_id", "district_name"]
    }
    query = QS(
        slug='walmart_stores',
        conditions=conditions,
        program='walmart',
        output_format='polars'
    )
    result, _ = await query.dry_run()
    ended = datetime.now()
    generated_at = (ended - started).total_seconds()
    print(':: SQL ', result)
    print('Parsing Time: ', generated_at)
    print(' === Execute Query === ')
    result, error = await query.query()
    print(result, error)
    print(type(result))


if __name__ == '__main__':
    asyncio.run(start())
