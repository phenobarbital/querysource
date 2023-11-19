import asyncio
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from asyncdb.drivers.pg import pgPool
from querysource.conf import default_dsn

async def execute_query(query, pool):
    async with await pool.acquire() as conn:
        if query.startswith('SELECT') or query.startswith('select'):
            result = await conn.query(query)
        else:
            result = await conn.execute(query)
        return result

async def query():
    pool = pgPool(dsn=default_dsn)
    await pool.connect()

    session = PromptSession(history=InMemoryHistory())
    while True:
        try:
            query = await session.prompt_async("QUERY > ")
            if query.lower() in ["exit", "quit"]:
                break
            result = await execute_query(query, pool)
            print(result)
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            print(
                f"An error occurred: {e}"
            )

    await pool.close()

def main():
    asyncio.run(query())
