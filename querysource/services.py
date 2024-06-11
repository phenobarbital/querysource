import sys
import subprocess
import asyncio
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from pkgutil import iter_modules
from aiohttp import web
try:
    import nbformat
    from nbconvert import HTMLExporter
    JUPYTER_ENABLED = True
except ImportError:
    JUPYTER_ENABLED = False
from navconfig.logging import logging
from navigator.applications.base import BaseApplication
from navigator.types import WebApp
# QS
from .datasources.handlers import DatasourceDrivers, DatasourceView
from .template import TemplateParser
from .types import Singleton
from .handlers import (
    QueryService,
    QueryHandler,
    QueryExecutor,
    QueryManager,
    VariablesService,
    LoggingService
)
from .conf import (
    ENABLED_JUPYTER,
    JUPYTER_PORT,
    JUPYTER_TOKEN,
    JUPYTER_CONFIG,
    JUPYTER_LAB_DIR
)

try:
    from settings.settings import QUERYSOURCE_FILTERS, QUERYSOURCE_VARIABLES
except ImportError:
    QUERYSOURCE_FILTERS = {}
    QUERYSOURCE_VARIABLES = {}

from .interfaces.connections import PROVIDERS
from .connections import QueryConnection
from .parsers import QS_VARIABLES, QS_FILTERS


async def notebook_handler(request):
    notebook_name = request.match_info['name']
    notebook_path = JUPYTER_LAB_DIR.joinpath(f'{notebook_name}.ipynb')

    if not notebook_path.exists():
        return web.Response(
            status=404,
            text=f"Notebook not found at {notebook_path}"
        )

    with open(notebook_path) as f:
        nb = nbformat.read(f, as_version=4)
        exporter = HTMLExporter()
        body, resources = exporter.from_notebook_node(nb)

    return web.Response(text=body, content_type='text/html')

class QuerySource(metaclass=Singleton):
    """QuerySource.

    QuerySource is an Application (based on aiohttp)
    to processing data with different kind of Data Providers.
    We are already to work with:
    - Database providers (RDBMS using a SQLParser)
    - noSQL Providers (like Mongo, Cassandra or RethinkDB)
    - HTTP Providers (scrappers)
    - RESTful Provider (getting data from RESTful APIs)
    - Hypertext-Queries: making queries directly in the URL.
    - GrapQL Provider (minimal support for making graphQL queries to different backends - using datasources -)
    """
    jupyter_process = None

    def __init__(self, **kwargs):
        if hasattr(self, '__initialized__'):
            if self.__initialized__ is True:
                return  # already configured.
        self.lazy: bool = kwargs.get('lazy', False)
        self._loop: asyncio.AbstractEventLoop = kwargs.get('loop', asyncio.get_event_loop())
        ### Connection Object:
        self.connection = QueryConnection(loop=self._loop, lazy=self.lazy)
        ### Loading all providers when started:
        path = Path(__file__).parent.joinpath('providers')
        for (_, name, ispkg) in iter_modules([str(path)]):
            if name != 'abstract' and not ispkg:
                cls = self.connection.load_provider(name)
                PROVIDERS[name] = cls
        for name, fn in QUERYSOURCE_FILTERS.items():
            if func := self.load_library(fn):
                QS_FILTERS[name] = func
        for name, fn in QUERYSOURCE_VARIABLES.items():
            if func := self.load_library(fn):
                QS_VARIABLES[name] = func

    def setup(self, app: web.Application) -> web.Application:
        if isinstance(app, BaseApplication):  # migrate to BaseApplication (on types)
            self.app = app.get_app()
        elif isinstance(app, WebApp):
            self.app = app  # register the app into the Extension
        # register the Connection Object:
        self.connection.setup(app=app)
        ## Start the template System
        tpl = TemplateParser()
        tpl.setup(app=app)
        ## making the registration of handlers (services and managers)
        qs = QueryService()
        routes = []
        ## user-interface to run queries:
        r = self.app.router.add_get('/api/v2/services/queries', qs.run_queries, allow_head=True)
        routes.append(r)
        # named-queries
        r = self.app.router.add_get('/api/v2/test/queries/{slug}', qs.test_slug, allow_head=True)
        routes.append(r)
        r = self.app.router.add_post('/api/v2/test/queries/{slug}', qs.test_slug)
        routes.append(r)
        ## Test query without saving (also: running)
        r = self.app.router.add_post('/api/v2/test/queries', qs.run_query)
        routes.append(r)
        r = self.app.router.add_get('/api/v2/services/queries/{slug}', qs.query, allow_head=True)
        routes.append(r)
        r = self.app.router.add_post('/api/v2/services/queries/{slug}', qs.query)
        routes.append(r)
        # get columns:
        r = self.app.router.add_patch('/api/v2/services/queries/{slug}', qs.columns)
        routes.append(r)

        ### Query Executor:
        ds = QueryExecutor()  # Support for Driver management.
        r = self.app.router.add_post('/api/v1/queries/test', ds.dry_run)
        routes.append(r)
        r = self.app.router.add_post('/api/v1/queries/run', ds.query)
        routes.append(r)

        ### Logging Service:
        lg = LoggingService()
        r = self.app.router.add_get('/api/v1/audit_log', lg.audit_log)
        routes.append(r)

        ### Query Manager ###
        r = self.app.router.add_view(
            r'/api/v1/management/queries/{slug}', QueryManager
        )
        routes.append(r)
        r = self.app.router.add_view(
            r'/api/v1/management/queries{meta:\:?.*}', QueryManager
        )
        routes.append(r)

        ## Multi-Query:
        mq = QueryHandler()
        r = self.app.router.add_post(
            r'/api/v3/queries{meta:\:?.*}',
            mq.query
        )
        routes.append(r)

        # querying directly to drivers
        # self.app.router.add_get('/api/v2/queries/{driver}/{method}', qs.query)
        # self.app.router.add_post('/api/v2/queries/{driver}/{method}', qs.query)
        r = self.app.router.add_get('/api/v2/queries/{driver}/{source}/{attribute}', qs.query, allow_head=True)
        routes.append(r)
        r = self.app.router.add_post('/api/v2/queries/{driver}/{source}/{attribute}', qs.query)
        routes.append(r)
        # with in-url parameters:
        r = self.app.router.add_get('/api/v2/queries/{driver}/{source}/{attribute}/{var:.*}', qs.query, allow_head=True)
        routes.append(r)
        r = self.app.router.add_post('/api/v2/queries/{driver}/{source}/{attribute}/{var:.*}', qs.query)
        routes.append(r)

        ## Datasource Support
        ds = DatasourceDrivers()  # Support for Driver management.
        r = self.app.router.add_get('/api/v1/datasources/drivers/list', ds.supported_drivers)
        routes.append(r)
        r = self.app.router.add_get('/api/v1/datasources/driver/{driver}', ds.get_driver)
        routes.append(r)
        r = self.app.router.add_post('/api/v1/datasources/driver/{driver}', ds.check_credentials)
        routes.append(r)
        r = self.app.router.add_put('/api/v1/datasources/driver/{driver}', ds.test_connection)
        routes.append(r)
        ## managing datasources:
        r = self.app.router.add_view('/api/v1/datasources', DatasourceView)
        routes.append(r)
        r = self.app.router.add_view('/api/v1/datasources/{filter}', DatasourceView)
        routes.append(r)
        ### add or modify datasources:
        r = self.app.router.add_view('/api/v1/datasource', DatasourceView)
        routes.append(r)
        r = self.app.router.add_view('/api/v1/datasource/{source}', DatasourceView)
        routes.append(r)

        ### Getting the QuerySource Extensions
        ### Getting the QuerySource variables
        # PROGRAM Variables
        self.app.router.add_view("/api/v2/variables", VariablesService)
        self.app.router.add_route(
            "*", "/api/v2/services/variables", VariablesService
        )
        self.app.router.add_view("/api/v2/variables/{program}", VariablesService)
        self.app.router.add_view(
            "/api/v2/variables/{program}/{variable}", VariablesService
        )

        ## Add Jupyter Notebooks
        if ENABLED_JUPYTER is True:
            # Dynamically add routes for each notebook
            route_path = '/qs/reports/lab/{name}'
            self.app.router.add_get(
                route_path,
                notebook_handler,
                allow_head=False
            )

        ### Startup Event for QuerySource:
        self.app.on_startup.append(
            self.qs_start
        )
        self.app.on_shutdown.append(
            self.qs_stop
        )

    def event_loop(self):
        return self._loop

    def load_library(self, function: str) -> Callable:
        classpath, fn = function.rsplit('.', 1)
        func = None
        try:
            mdl = import_module(classpath, package=classpath)
            func = getattr(mdl, fn)
        except (ImportError, AttributeError) as err:
            logging.exception(
                f"Cannot load Function {fn} from package: {classpath}: {err}",
                stack_info=False
            )
        return func

    async def jupyter_start(self):
        if ENABLED_JUPYTER:
            # Command to start Jupyter
            command = [
                sys.executable, '-m', 'notebook', f'--config={JUPYTER_CONFIG}',
                '--no-browser', '--NotebookApp.allow_origin=https://colab.research.google.com',
                f'--port={JUPYTER_PORT}', '--NotebookApp.port_retries=0',
                f"--NotebookApp.token='{JUPYTER_TOKEN}'", "--ServerApp.root_dir=./lab/"
            ]
            # Start Jupyter as a subprocess
            self.jupyter_process = subprocess.Popen(command)
            print(
                f"Jupyter Server started with PID: {self.jupyter_process.pid}"
            )

    async def jupyter_stop(self):
        if self.jupyter_process:
            # Terminate the Jupyter process
            self.jupyter_process.terminate()
            print("Jupyter Server stopped")

    async def qs_start(self, app: WebApp) -> None:
        if not self._loop:
            self._loop = asyncio.get_event_loop()
        await self.jupyter_start()

    async def qs_stop(self, app: WebApp) -> None:
        await self.jupyter_stop()
