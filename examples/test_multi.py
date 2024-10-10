from typing import Union
from collections.abc import Callable, Awaitable
import time
import asyncio
import threading



async def test_query(num: int, queue: asyncio.Queue):
    result = {
        "result": num
    }
    print('RESULT > ', result)
    await asyncio.sleep(1)
    await queue.put(result)


class MultiQuery(threading.Thread):
    def __init__(self, task: Union[Awaitable, Callable]):
        super().__init__()
        self._loop = asyncio.new_event_loop()
        self._task = task

    def run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(
            self._task
        )

async def main():
    total_time = 0
    started_at = time.monotonic()
    result_queue = asyncio.Queue()
    tasks = [
        test_query(42, result_queue),
        test_query(24, result_queue),
        test_query(56, result_queue),
        test_query(21, result_queue),
        test_query(82, result_queue),
    ]
    multi = []
    for task in tasks:
        t = MultiQuery(task)
        t.start()
        multi.append(t)

    for t in multi:
        t.join()
    results = []
    while not result_queue.empty():
        results.append(await result_queue.get())
    print(results)
    total_time = time.monotonic() - started_at
    print(f'total expected time: {total_time:.2f} seconds')

asyncio.run(main())
