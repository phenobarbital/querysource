from aiohttp import web

# new QuerySource Connection Manager (supporting datasources)
from querysource.services import QuerySource

qry = QuerySource(lazy=False)

async def handle(request):
    name = request.match_info.get('name', "Anonymous")
    text = "Hello, " + name
    return web.Response(text=text)

app = web.Application()

qry.setup(app=app) # adding QS to the Application (using App Signals)

app.add_routes([web.get('/', handle),
                web.get('/{name}', handle)])


# # Managing Queries.
# # app.router.add_view('/api/v2/queries/', QueryManager)
# # app.router.add_view('/api/v2/queries/{slug}', QueryManager)

# qs = QueryService()
# # # test any arbitrary query slug:
# # app.router.add_post('/api/v2/services/queries', qs.test_query)

# # named-queries
# app.router.add_post('/api/v2/test/queries/{slug}', qs.test_slug) # dry-run
# app.router.add_get('/api/v2/services/queries/{slug}', qs.query, allow_head=True)
# app.router.add_post('/api/v2/services/queries/{slug}', qs.query)
# # app.router.add_patch('/api/v2/services/queries/{slug}', qs.columns) # replacing path with another kind of GET

# # querying directly to drivers
# app.router.add_get('/api/v2/queries/{driver}/{method}', qs.query)
# app.router.add_post('/api/v2/queries/{driver}/{method}', qs.query)
# app.router.add_get('/api/v2/queries/{driver}/{source}/{method}', qs.query, allow_head=True)
# app.router.add_post('/api/v2/queries/{driver}/{source}/{method}', qs.query)

# # with in-url parameters:
# app.router.add_get('/api/v2/queries/{driver}/{source}/{method}/{var:.*}', qs.query, allow_head=True)
# app.router.add_post('/api/v2/queries/{driver}/{source}/{method}/{var:.*}', qs.query)


if __name__ == '__main__':
    try:
        web.run_app(
            app, host='localhost', port=5000, handle_signals=True, loop=qry.event_loop()
        )
    except KeyboardInterrupt:
        print('EXIT FROM APP =========')
