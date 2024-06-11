from aiohttp import web
from querysource.handlers import LoggingService


async def handle(request):
    log = LoggingService()
    return await log.audit_log(
        request=request,
        use_geloc=False,
        event_name='Test Event'
    )


app = web.Application()
app.add_routes([web.get('/log', handle)])


if __name__ == '__main__':
    try:
        web.run_app(
            app, host='localhost', port=5000, handle_signals=True
        )
    except KeyboardInterrupt:
        print('EXIT FROM APP =========')
