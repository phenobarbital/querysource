import asyncio
import uvloop


def enable_uvloop():
    try:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        uvloop.install()
        return True
    except ImportError:
        return False


async def log_task(task):
    print(f'Task {task} finished with result {task.result()}')


def log_all_tasks(loop):
    for task in asyncio.all_tasks(loop):
        task.add_done_callback(lambda t: loop.create_task(log_task(t)))
