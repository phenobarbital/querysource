import asyncio
from datetime import datetime
from querysource.queries.qs import QS


async def start():
    started = datetime.now()
    conditions = {
        "orgid": 106,
        "refresh": 1,
        "formid": 3820,
        "firstdate": "2023-09-15 00:00:00",
        "lastdate": "2023-09-25 00:00:00",
    }
    query = QS(
        slug='vision_form_data',
        conditions=conditions,
        program='hisense'
    )
    result, _ = await query.dry_run()
    ended = datetime.now()
    generated_at = (ended - started).total_seconds()
    print(':: SQL ', result)
    print('Parsing Time: ', generated_at)
    print(' === Execute Query === ')
    result, error = await query.query()
    print(type(result))


if __name__ == '__main__':
    asyncio.run(start())
