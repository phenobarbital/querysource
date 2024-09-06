from querysource.queries import QS
import asyncio


async def query():
    query = QS(
        slug='dvdrental_actors'
    )
    await query.build_provider()
    result, error = await query.query()
    print(result, error)
    print(type(result))

if __name__ == '__main__':
    asyncio.run(query())
