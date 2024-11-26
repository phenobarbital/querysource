import asyncio
from datetime import datetime
import socket
from asyncdb import AsyncDB
from navconfig.logging import logging, logger
from ..conf import (
    QS_EVENT_BACKEND,
    QS_EVENT_CREDENTIALS,
    QS_EVENT_TABLE,
    ENVIRONMENT,
    INFLUX_DATABASE,
    USE_INFLUX
)

EVENT_HOST = socket.gethostbyname(socket.gethostname())

async def LogEvent(
    payload: dict,
    status: str = None,
    **kwargs
):
    """LogEvent.

    Logging Facility for QuerySource.
    """
    logger.info(
        f"Logging Event: {payload!s}",
        extra={
            "status": status,
            "host": EVENT_HOST,
            "timestamp": datetime.utcnow(),
            "region": ENVIRONMENT,
            **kwargs
        }
    )
    # TODO: migrate to a worker task to avoid blocking the main thread.
    # _new = False
    # if USE_INFLUX is True:
    #     try:
    #         event_loop = asyncio.get_event_loop()
    #     except RuntimeError:
    #         event_loop = asyncio.new_event_loop()
    #         asyncio.set_event_loop(event_loop)
    #         _new = True
    #     influx = AsyncDB(
    #         QS_EVENT_BACKEND,
    #         params=QS_EVENT_CREDENTIALS,
    #         loop=event_loop
    #     )
    #     if status is None:
    #         status = 'event'
    #     try:
    #         async with await influx.connection() as conn:
    #             try:
    #                 # saving the log into metric database:
    #                 start_time = datetime.utcnow()
    #                 data = {
    #                     "measurement": QS_EVENT_TABLE,
    #                     "location": ENVIRONMENT,
    #                     "timestamp": start_time,
    #                     "fields": {
    #                         "status": status
    #                     },
    #                     "tags": {
    #                         "host": EVENT_HOST,
    #                         "region": ENVIRONMENT,
    #                         "start_time": start_time,
    #                         **payload,
    #                         **kwargs
    #                     }
    #                 }
    #                 await conn.write(data, bucket=INFLUX_DATABASE)
    #             except Exception as e:
    #                 logging.error(
    #                     f'DI: Error saving Query Execution: {e}'
    #                 )
    #     finally:
    #         if _new is True:
    #             event_loop.close()
