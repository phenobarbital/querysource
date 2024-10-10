import asyncio
import uvloop
from aiohttp import web
from querysource.datasources.handlers import DatasourceUtils, DatasourceView

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
uvloop.install()

loop = asyncio.get_event_loop()
asyncio.set_event_loop(loop)

app = web.Application()

ds = DatasourceUtils() # Support for Driver management.
app.router.add_get('/api/v1/datasources/drivers/list', ds.supported_drivers)
app.router.add_get('/api/v1/datasources/driver/{driver}', ds.get_driver)
app.router.add_post('/api/v1/datasources/driver/{driver}', ds.check_credentials)
app.router.add_put('/api/v1/datasources/driver/{driver}', ds.test_connection)
## managing datasources:
app.router.add_view('/api/v1/datasources', DatasourceView)
app.router.add_view('/api/v1/datasources/{filter}', DatasourceView)
app.router.add_view('/api/v1/datasource/{source}', DatasourceView)



if __name__ == '__main__':
    try:
        web.run_app(
            app, host='localhost', port=5000, handle_signals=True, loop=loop
        )
    except KeyboardInterrupt:
        print('EXIT FROM APP =========')
