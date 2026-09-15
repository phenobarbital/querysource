from typing import Any
from influxdb_client import Point
import requests
import socket
from asyncdb import AsyncDB
from asyncdb.exceptions.exceptions import DriverError
from aiohttp import web
from navigator_session import get_session
from navigator.views import BaseHandler
from ..conf import (
    ENVIRONMENT,
    INFLUX_HOST,
    INFLUX_PORT,
    INFLUX_USER,
    INFLUX_PWD,
    INFLUX_LOGGING,
    INFLUX_TOKEN,
    INFLUX_ORG,
    GEOLOC_API_KEY
)


EVENT_HOST = socket.gethostbyname(socket.gethostname())


class LoggingService(BaseHandler):

    def _db(self, driver: str = 'influx'):
        return AsyncDB(
            driver,
            params={
                'host': INFLUX_HOST,
                'port': INFLUX_PORT,
                'user': INFLUX_USER,
                'password': INFLUX_PWD,
                'bucket': INFLUX_LOGGING,
                'token': INFLUX_TOKEN,
                'org': INFLUX_ORG
            }
        )

    def prepare_point(
        self,
        tags: list[tuple],
        fields: list[tuple],
        log_name: str = 'audit_log',
        session: Any = None,
        **kwargs
    ):
        point = Point(log_name)
        for tag in tags:
            point.tag(*tag)

        for field in fields:
            point.field(*field)

        if session:
            try:
                point.tag('session_id', session.id)
                point.tag('session_username', session.username)
            except Exception:
                pass

        for key, value in kwargs.items():
            point = point.field(key, str(value))

        return point

    async def session_info(self, request):
        try:
            return await get_session(request)
        except RuntimeError:
            return None

    async def request_info(self, request: web.Request):
        """Build the audit info dict; add verified ownership fields when available.

        The HTTP audit path joins the same owner/schema/table identity used
        by background work (TASK-731 AC-2): if the request carries a
        resolved tenant selector (``request['qs_tenant']``, set by
        ``TenantQueryHandler.query()``), it is resolved against the app's
        own tenant registry — never guessed from the URL — and the
        resulting owner fields are added. Absent a tenant selector or a
        published registry, the existing audit structure is returned
        unchanged (no forced fields, no secrets).
        """
        ip = request.remote
        info = {
            'method': request.method,
            'path': request.path,
            'query_string': str(request.query_string),
            'user_agent': request.headers.get("User-Agent", ""),
            'host': request.host,
            'url': str(request.url),
        }
        tenant = request.get('qs_tenant')
        registry = getattr(request, 'app', None) and request.app.get('qs_tenant_registry')
        if tenant is not None and registry is not None:
            try:
                store = registry.resolve(tenant)
                info['owner'] = store.schema
                info['schema'] = store.schema
                info['table'] = store.table
            except Exception:  # noqa: BLE001 - audit logging must never break on a stale/removed tenant
                info['owner'] = tenant
        elif tenant is not None:
            info['owner'] = tenant
        return ip, info

    async def get_geolocation(self, ip):
        url = f"https://api.ipgeolocation.io/ipgeo?apiKey={GEOLOC_API_KEY}&ip={ip}"
        response = requests.get(url)
        if response.status_code == 200:
            return response.json()
        else:
            print(response.status_code, response)
            return None

    async def audit_log(self, request: web.Request, **kwargs):
        ip, additional_info = await self.request_info(request)
        self.logger.info(f':: Audit IP : {ip}')
        args = self.get_arguments(request)
        session = await self.session_info(request)
        use_geloc = kwargs.pop('use_geloc', True)
        geolocation = {}
        if use_geloc:
            geolocation = await self.get_geolocation(ip)
            if geolocation:
                additional_info.update({
                    "country": geolocation.get("country_name", ""),
                    "region": geolocation.get("state_prov", ""),
                    "city": geolocation.get("city", ""),
                    "latitude": geolocation.get("latitude", ""),
                    "longitude": geolocation.get("longitude", "")
                })
        data = await self.data(request)
        event_name = kwargs.get('event_name', args.get('event_name', 'Audit Log'))
        try:
            final_data = {**data, **kwargs}
            final_data.update(additional_info)
        except (KeyError, TypeError, AttributeError):
            final_data = {**kwargs, **additional_info}
        point = self.prepare_point(
            fields=[
                ('event_name', event_name),
            ],
            tags=[
                ('ip', ip),
                ("host", EVENT_HOST),
                ("region", ENVIRONMENT),
            ],
            session=session,
            **final_data,
        )
        async with await self._db().connection() as conn:
            try:
                await conn.create_database(INFLUX_LOGGING)
            except DriverError:
                pass
            await conn.write(bucket=INFLUX_LOGGING, data=[point])
            headers = {
                'X-STATUS': 'OK',
                'X-MESSAGE': 'Event Logged'
            }
            response = {
                'status': 'OK',
                'message': 'Event Logged'
            }
            return self.json_response(
                response=response,
                headers=headers,
                status=202
            )
