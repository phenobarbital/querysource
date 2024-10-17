import asyncio
from datetime import datetime
from querysource.queries.qs import QS


async def leads():
    started = datetime.now()
    conditions = {
        "fields": ["Address", "Bio", "Company", "Email"]
    }
    query = QS(
        query='SELECT {fields} FROM Lead',
        conditions=conditions,
        driver='salesforce',

    )
    await query.build_provider()
    result, _ = await query.dry_run()
    ended = datetime.now()
    generated_at = (ended - started).total_seconds()
    print('Parsing Time: ', generated_at)
    print(' === Execute Query === ')
    result, error = await query.query()
    print(len(result), error)
    print(type(result))
    print('SAMPLE RESULT ', result[0])


async def accounts():
    started = datetime.now()
    conditions = {
    }
    query = QS(
        query='SELECT {fields} FROM Account',
        conditions=conditions,
        driver='salesforce'
    )
    print(' === Execute Query === ')
    result, error = await query.query()
    ended = datetime.now()
    generated_at = (ended - started).total_seconds()
    print(len(result), error)
    print(type(result))
    print('SAMPLE RESULT ', result[0])
    print('Parsing Time: ', generated_at)

async def opportunities():
    started = datetime.now()
    conditions = {
    }
    query = QS(
        query='SELECT {fields} FROM Opportunity',
        conditions=conditions,
        driver='salesforce'
    )
    print(' === Execute Query === ')
    result, error = await query.query()
    ended = datetime.now()
    generated_at = (ended - started).total_seconds()
    print(len(result), error)
    print(type(result))
    print('SAMPLE RESULT ', result[0])
    print('Parsing Time: ', generated_at)

async def contacts():
    started = datetime.now()
    conditions = {
    }
    query = QS(
        query='SELECT {fields} FROM Contact',
        conditions=conditions,
        driver='salesforce'
    )
    print(' === Execute Query === ')
    result, error = await query.query()
    ended = datetime.now()
    generated_at = (ended - started).total_seconds()
    print(len(result), error)
    print(type(result))
    print('SAMPLE RESULT ', result[0])
    print('Parsing Time: ', generated_at)

# asyncio.run(leads())
# asyncio.run(accounts())
# asyncio.run(opportunities())
asyncio.run(contacts())
