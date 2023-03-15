import asyncio
import uvloop
from navigator import Application

from app import Main


async def navigator():
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    uvloop.install()
    # define new Application
    app = Application(Main)
    # returns App.
    return app.setup()
    # return app
