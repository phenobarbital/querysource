import inspect
import logging
import re
from functools import partial
import uuid
from importlib import import_module
from aiohttp import web
from datamodel.exceptions import ValidationError
from asyncdb import AsyncDB
from asyncdb.exceptions import (
    ProviderError,
    DriverError,
    NoDataFound,
    StatementError
)
from navigator.views import BaseView
from navigator_session import get_session
from navigator_auth.decorators import (
    is_authenticated,
    user_session
)
from ...conf import default_dsn
from ...utils.functions import anonymize
from ...utils.parseqs import ParseDict
from ...exceptions import ParserError
from ..models import DataSource
from ..drivers import SUPPORTED
from ...interfaces.connections import DATASOURCES
from ...auth import ResourceType

# Module-level fallback logger for paths that may run before BaseView's
# ``_logger`` attribute is initialised (e.g. during unit tests that construct
# the view without going through the full request lifecycle).
_PBAC_LOGGER = logging.getLogger("querysource.datasources.handlers.datasource")

# Secret-bearing credential keys that must be redacted in API responses.
_SECRET_KEYS: frozenset[str] = frozenset({
    "password", "pwd", "secret", "token", "api_key", "apikey", "key",
    "access_token", "refresh_token", "client_secret", "private_key",
    "passphrase", "auth_token", "bearer", "credential", "credentials",
})

# Pattern to detect and mask user:password pairs in DSN strings,
# e.g. "postgres://user:s3cret@host:5432/db" → "postgres://user:****@host:5432/db"
_DSN_USERINFO_RE = re.compile(r"(://)([^:@/]+):([^@]+)(@)", re.ASCII)


def _redact_datasource(record: dict) -> dict:
    """Return a COPY of ``record`` with all secret values replaced by '(hidden)'.

    Redacts:
    - Every key in ``credentials`` whose name is in ``_SECRET_KEYS``.
    - Any user:password pair embedded in the ``dsn`` field.

    Does NOT mutate the original ``record`` or any nested dicts in place.
    Works for both plain ``dict`` records (from ``default_sources()``) and
    dict-serialised ``DataSource`` Model instances.

    Args:
        record: A datasource record dict (may be a shallow copy already).

    Returns:
        A new dict with secrets redacted.
    """
    out = dict(record)  # shallow copy at top level

    # Redact credentials dict
    creds = out.get("credentials")
    if isinstance(creds, dict):
        out["credentials"] = {
            k: "(hidden)" if k in _SECRET_KEYS else v
            for k, v in creds.items()
        }

    # Redact or remove dsn
    dsn = out.get("dsn")
    if isinstance(dsn, str) and dsn:
        # Replace "://user:secret@" with "://user:****@"
        out["dsn"] = _DSN_USERINFO_RE.sub(r"\g<1>\g<2>:****\g<4>", dsn)

    return out


async def _check_datasource_read(request: web.Request, logger=None) -> None:
    """Fail-closed PBAC gate for datasource:read.

    Modelled on ``AbstractHandler._enforce_pbac`` (handlers/abstract.py:259).
    ``DatasourceView`` extends ``BaseView`` (not ``AbstractHandler``), so
    ``_enforce_pbac`` is NOT available here — we replicate the logic.

    Fast-path no-op when ``app['security']`` is absent (PBAC disabled).
    When PBAC is enabled but no session can be read, raises 404 (deny).

    Args:
        request: The current aiohttp web request.
        logger: Optional logger; falls back to module-level ``_PBAC_LOGGER``.

    Raises:
        web.HTTPNotFound: When PBAC is enabled but session or evaluator is
            missing (fail-closed).
    """
    _log = logger or _PBAC_LOGGER
    guardian = request.app.get("security")
    if guardian is None:
        return  # PBAC disabled — fast-path no-op

    # Attempt to extract the user session.
    # Use navigator_session.get_session() — the same proven path as
    # AbstractHandler._get_user_session (handlers/abstract.py:301). The
    # session storage object under ``app['session']`` is NOT the right
    # API here and was silently failing, denying valid sessions.
    session = None
    try:
        session = await get_session(request, new=False)
    except RuntimeError:
        _log.error("QS: User Session system is not installed.")

    if session is None:
        _log.info(
            "PBAC denied (no session): datasource/datasource:read"
        )
        raise web.HTTPNotFound()

    evaluator = request.app.get("policy_evaluator")
    if evaluator is None:
        _log.error(
            "PBAC misconfigured: 'security' is set but 'policy_evaluator' is missing"
        )
        raise web.HTTPNotFound()

    # Evaluate access using the same pattern as AbstractHandler._enforce_pbac.
    try:
        from navigator_auth.abac.context import EvalContext
        from navigator_auth.abac.policies.environment import Environment
        from navigator_auth.conf import AUTH_SESSION_OBJECT
    except ImportError:
        # navigator_auth not installed; skip PBAC check
        return

    userinfo = (
        session.get(AUTH_SESSION_OBJECT, {})
        if hasattr(session, "get") else {}
    )
    if not isinstance(userinfo, dict):
        userinfo = {}
    user = userinfo if userinfo else None

    ctx = EvalContext(
        request=request,
        user=user,
        userinfo=userinfo,
        session=session,
    )
    result = evaluator.check_access(
        ctx=ctx,
        resource_type=ResourceType.DATASOURCE,
        resource_name="datasource",
        action="datasource:read",
        env=Environment(),
    )
    if inspect.iscoroutine(result):
        result = await result
    if not result.allowed:
        _log.info(
            "PBAC denied: datasource/datasource:read policy=%s reason=%s",
            getattr(result, "matched_policy", None),
            getattr(result, "reason", None),
        )
        raise web.HTTPForbidden()


def _item_get(item, key, default=None):
    """Read ``key`` from either a dict or an asyncdb Model instance.

    Why: ``DataSource.all()`` returns Model instances whose ``.get`` is the
    classmethod that fetches a row by primary key — calling ``item.get('x')``
    on it raises ``TypeError``. ``default_sources()`` on the other hand
    returns plain dicts, so we need a uniform accessor.
    """
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


@user_session()
class DatasourceView(BaseView):
    """API View for managing datasources.
    """

    async def _pbac_filter(
        self,
        request: web.Request,
        items: list,
        name_key: str,
        resource_type,
        action: str,
    ) -> list:
        """Filter a list of dicts by PBAC; silent no-op when PBAC disabled.

        Returns the filtered list (may be empty). Never raises on empty results.
        Status 200 is the caller's responsibility.

        Args:
            request: The current aiohttp web request.
            items: List of result dicts to filter.
            name_key: Key name to use for extracting resource names.
            resource_type: ResourceType (or string shim) for the check.
            action: PBAC action string (e.g. "datasource:list").

        Returns:
            Filtered list containing only allowed items.
        """
        guardian = request.app.get('security')
        if guardian is None:
            return items  # PBAC disabled — return all
        if not items:
            return items
        names = [_item_get(item, name_key) for item in items if _item_get(item, name_key)]
        if not names:
            return items
        try:
            result = await guardian.filter_resources(
                resources=names,
                request=request,
                resource_type=resource_type,
                action=action,
            )
            allowed = set(result.allowed)
        except Exception as exc:
            # Fail-open for listing endpoints: if guardian errors, return
            # unfiltered. We log a warning so operators can detect a broken
            # guardian — silent fail-open would hide policy-engine outages.
            log = getattr(self, "_logger", None) or _PBAC_LOGGER
            log.warning(
                "PBAC list filtering failed (%s/%s): %s. "
                "Returning unfiltered list (fail-open).",
                resource_type,
                action,
                exc,
            )
            return items
        return [item for item in items if _item_get(item, name_key) in allowed]

    def model(self):
        return DataSource

    def get_connection(self):
        return AsyncDB('pg', dsn=default_dsn)

    def default_sources(self) -> list:
        drivers = []
        for name, _ in SUPPORTED.items():
            try:
                clspath = f'querysource.datasources.drivers.{name}'
                cls = import_module(clspath)
                clsname = f'{name}_default'
                drv = getattr(cls, clsname)
                if not drv:
                    continue
                credentials = drv.get_credentials()
                if 'password' in credentials:
                    credentials['password'] = anonymize(
                        credentials['password']
                    )
                params = drv.get_parameters()
                if 'password' in params:
                    params['password'] = anonymize(
                        params['password']
                    )
                driver = {
                    "uid": uuid.uuid1(),
                    "driver": drv.driver,
                    "name": name,
                    "description": drv.name,
                    "params": params,
                    "credentials": credentials,
                    "program_slug": "default",
                    "drv": drv.modelName,
                    "default": True
                }
                if hasattr(drv, 'dsn_format'):
                    driver['dsn'] = drv.dsn_format
                if hasattr(drv, 'icon'):
                    driver['icon'] = drv.icon
                drivers.append(driver)
            except (AttributeError, ImportError) as ex:
                print(ex)
                continue
        return drivers

    async def get(self) -> web.Response:
        """
        GET Method.
        description: get all datasources, or a datasource by ID or name
        tags:
        - datasources
        - Database connections
        consumes:
        - application/json
        produces:
        - application/json
        responses:
            "200":
                description: Existing Datasource was retrieved.
            "403":
                description: Forbidden Call
            "404":
                description: No datasource(s) were found
            "406":
                description: Query Error
        """
        # SECURITY (FEAT-103): Fail-closed datasource:read PBAC gate.
        # When PBAC is enabled but no session/evaluator is present, deny.
        # When PBAC is disabled (security absent), this is a fast no-op.
        log = getattr(self, "_logger", None) or _PBAC_LOGGER
        await _check_datasource_read(self.request, logger=log)

        filtering = None
        ds = None
        try:
            arg = self.get_arguments(self.request)
            if 'source' in arg:
                ds = arg['source']
            if 'filter' in arg:
                try:
                    filtering = ParseDict(arg['filter'])
                except ParserError:
                    return self.error(
                        status=401,
                        response={"error": "Wrong Filter QS, please check query-string Filter."}
                    )
        except (KeyError, ValueError):  # pylint: disable=W0703
            ds = None
        # getting all datasources based on ds variable:
        db = self.get_connection()
        fields = ["uid", "driver", "name", "description", "params", "credentials", "dsn", "program_slug"]
        if not ds:
            try:
                async with await db.connection() as conn:
                    if not filtering:
                        fn = partial(DataSource.all, _connection=conn, fields=fields)
                    else:
                        fn = partial(DataSource.filter, _connection=conn, **filtering, fields=fields)
                    try:
                        result = await fn()
                        headers = {
                            'X-STATUS': 'OK',
                            'X-MESSAGE': 'Datasource Information'
                        }
                        default = self.default_sources()
                        if not filtering:
                            result = result + default
                        # PBAC: filter datasources and default drivers silently.
                        # DB-backed datasources filtered by datasource:list.
                        # Default-driver entries (default=True) filtered by driver:list.
                        db_items = [r for r in result if not _item_get(r, 'default')]
                        drv_items = [r for r in result if _item_get(r, 'default')]
                        db_items = await self._pbac_filter(
                            self.request, db_items, "name",
                            ResourceType.DATASOURCE, "datasource:list",
                        )
                        drv_items = await self._pbac_filter(
                            self.request, drv_items, "name",
                            ResourceType.DRIVER, "driver:list",
                        )
                        result = db_items + drv_items
                        # SECURITY (FEAT-103): Redact all secret values before returning.
                        result = [
                            _redact_datasource(
                                item if isinstance(item, dict) else item.to_dict()
                            )
                            for item in result
                        ]
                        return self.json_response(response=result, headers=headers)
                    except (ValidationError) as ex:
                        error = {
                            "message": f"Data is bad on origin: {ex}",
                            "payload": ex.payload,
                            "status": 406
                        }
                        return self.error(
                            **error
                        )
            except (ProviderError, DriverError) as ex:
                return self.error(
                    response={"error": f"Database Connection Error: {ex}"},
                    status=401
                )
            finally:
                db = None
        else:
            # filter by one single driver:
            async with await db.connection() as conn:
                try:
                    result = await DataSource.get(name=ds, _connection=conn)
                    # SECURITY (FEAT-103): Redact ALL credential secrets (not just password).
                    result_dict = (
                        result if isinstance(result, dict) else result.to_dict()
                    )
                    result_dict = _redact_datasource(result_dict)
                    headers = {
                        'X-STATUS': 'OK',
                        'X-MESSAGE': f'Datasource Information: {ds}'
                    }
                    return self.json_response(response=result_dict, headers=headers)
                except (ValidationError) as ex:
                    error = {
                        "message": f"Data is bad on origin: {ex}",
                        "payload": ex.payload,
                        "status": 406
                    }
                    return self.error(
                        **error
                    )
                finally:
                    db = None

    def get_driver(self, data: dict):
        # checking for data:
        removed = ['uid', 'program_slug', 'created_at', 'updated_at', 'drv']
        if 'credentials' in data:
            data = {**data, **data['credentials']}
            del data['credentials']
        if 'params' in data:
            data = {**data, **data['params']}
            del data['params']
        try:
            drvname = data['driver']
            drv = SUPPORTED[drvname]['driver']
            args = {k: v for k, v in data.items() if k not in removed}
            driver = drv(**args)
            return [driver, drvname]
        except ValueError as ex:
            raise ValueError(
                f"Datasource: Value Error on Datasource data: {ex}"
            ) from ex
        except ValidationError:
            raise
        except KeyError as ex:
            raise RuntimeError(
                f"Datasource: error getting Driver definition: {ex}"
            ) from ex

    async def put(self):
        """
        PUT Method.
        description: inserting or updating a Datasource (if exists)
        tags:
        - Datasource
        - datasources
        - Database connections
        produces:
        - application/json
        consumes:
        - application/merge-patch+json
        - application/json
        responses:
            "200":
                description: Existing Datasource was updated.
            "201":
                description: New Datasource was inserted
            "400":
                description: Invalid resource according data schema
            "403":
                description: Forbidden Call
            "404":
                description: No Data was found
            "406":
                description: Query Error
            "409":
                description: Conflict, a constraint was violated
        """
        log = getattr(self, "_logger", None) or _PBAC_LOGGER
        await _check_datasource_read(self.request, logger=log)
        data = await self.json_data()
        ## first, getting the driver:
        try:
            driver, drvname = self.get_driver(data)
        except ValueError as ex:
            return self.error(
                response={
                    "error": f"{ex!s}"
                },
                status=400
            )
        except ValidationError as ex:
            return self.error(
                response={
                    "error": f"There are errors on Driver information: {ex!s}",
                    "payload": str(ex.payload),
                },
                status=400
            )
        except RuntimeError as ex:
            return self.error(
                response={
                    "error": f"{ex!s}"
                },
                status=400
            )
        # getting datasource
        try:
            try:
                program_slug = data['program_slug']
            except KeyError:
                program_slug = 'navigator'
            attributes = {
                "name": data['name'],
                "description": data['description'],
                "credentials": driver.auth,
                "params": driver.get_parameters(),
                "driver": drvname,
                "program_slug": program_slug,
                # "drv": driver # TODO: serialized driver
            }
            datasource = DataSource(**attributes)
        except ValidationError as ex:
            return self.error(
                response={
                    "message": f'Invalid dataSource using {attributes!s}',
                    "payload": str(ex.payload),
                },
                exception=ex,
                status=406
            )
        except Exception as err:
            return self.error(
                response={
                    "error": f'Datasouce exception using: {attributes!s}'
                },
                exception=err,
                status=400
            )
        try:
            db = self.get_connection()
            async with await db.connection() as conn:
                result = await datasource.insert(_connection=conn)
                if not result:
                    headers = {
                        'X-STATUS': 'ERROR',
                        'X-MESSAGE': f'Error Inserting {drvname} Information'
                    }
                    return self.error(
                        'Empty response on Inserting Datasource',
                        status=409
                    )
                headers = {
                    'X-STATUS': 'OK',
                    'X-MESSAGE': f'{drvname} Information'
                }
                # if was inserted, then datasource will be updated:
                try:
                    DATASOURCES[datasource.name] = driver
                except (ValueError, AttributeError):
                    self._logger.warning(
                        f"We cannot update DATASOURCES list with {datasource.name} datasource."
                    )
                return self.json_response(
                    response=datasource,
                    headers=headers,
                    status=201
                )
        except Exception as err:
            if 'duplicate key' in str(err):
                return self.error(
                    response={
                        "error": f'Duplicate Datasource: {err!s}',
                    },
                    exception=err,
                    status=409
                )
            else:
                return self.error(
                    response={
                        f'Error Inserting Datasource: {err!s}',
                    },
                    exception=err,
                    status=400
                )
        finally:
            await db.close()

    async def delete(self):
        """
        delete Method.
        description: Deleting a Datasource
        tags:
        - Datasource
        - datasources
        - Database connections
        produces:
        - application/json
        consumes:
        - application/json
        responses:
            "200":
                description: Existing Datasource was Deleted.
            "400":
                description: Invalid resource according data schema
            "403":
                description: Forbidden Call
            "404":
                description: No Data was found
            "406":
                description: Query Error
            "409":
                description: Conflict, a constraint was violated
        """
        log = getattr(self, "_logger", None) or _PBAC_LOGGER
        await _check_datasource_read(self.request, logger=log)
        data = await self.json_data()
        args = self.get_arguments(request=self.request)
        name = None
        uid = None
        try:
            uid = uuid.UUID(args['source'])
        except ValueError:
            name = args['source']
        if not name and not uid:
            try:
                uid = data['uid']
            except (TypeError, KeyError):
                name = data['name']
        if uid is not None:
            ds = {
                "uid": uid
            }
        else:
            ds = {
                "name": name
            }
        try:
            db = self.get_connection()
            async with await db.connection() as conn:
                try:
                    datasource = await DataSource.get(_connection=conn, **ds)
                    headers = {
                        'X-STATUS': 'OK',
                        'X-MESSAGE': f'{datasource.name} Information'
                    }
                    await datasource.delete(_connection=conn)
                    try:
                        del DATASOURCES[datasource.name]
                    except KeyError:
                        pass
                    return self.json_response(
                        response={"message": "Datasource Deleted", "filter": ds},
                        headers=headers,
                        status=203
                    )
                except NoDataFound:
                    return self.error(
                        response={
                            "message": f"Missing Datasource: {ds}"
                        },
                        status=404
                    )
                except (ProviderError, DriverError, StatementError) as ex:
                    return self.error(
                        response={
                            "message": f"Error deleting Datasource: {ds}",
                            "error": str(ex)
                        },
                        status=409
                    )
        finally:
            await db.close()

    async def post(self):
        """
        post Method.
        description: updating (or insert) a Datasource
        tags:
        - Datasource
        - datasources
        - Database connections
        produces:
        - application/json
        consumes:
        - application/json
        responses:
            "202":
                description: Existing Datasource was updated.
            "201:
                description: a New datasource was added.
            "400":
                description: Invalid resource according data schema
            "403":
                description: Forbidden Call
            "404":
                description: No Data was found
            "406":
                description: Query Error
            "409":
                description: Conflict, a constraint was violated
        """
        log = getattr(self, "_logger", None) or _PBAC_LOGGER
        await _check_datasource_read(self.request, logger=log)
        data = await self.json_data()
        ## first, getting the driver:
        try:
            driver, drvname = self.get_driver(data)
        except ValueError as ex:
            return self.error(
                response={
                    "error": f"{ex!s}"
                },
                status=400
            )
        except ValidationError as ex:
            return self.error(
                response={
                    "error": f"There are errors on Driver information: {ex!s}",
                    "payload": str(ex.payload),
                },
                status=400
            )
        except RuntimeError as ex:
            return self.error(
                response={
                    "error": f"{ex!s}"
                },
                status=400
            )
        args = self.get_arguments(request=self.request)
        name = None
        uid = None
        try:
            uid = str(uuid.UUID(args['source']))
        except KeyError:
            uid = None
        except ValueError:
            name = args['source']
        if not name and not uid:
            try:
                uid = data['uid']
            except (TypeError, KeyError):
                name = data['name']
        if uid is not None:
            ds = {
                "uid": uid
            }
        elif name is not None:
            ds = {
                "name": name
            }
        else:
            ds = None
        try:
            found = False
            db = self.get_connection()
            async with await db.connection() as conn:
                if ds is not None:
                    try:
                        dt = await DataSource.get(_connection=conn, **ds)
                        attributes = {**dt.to_dict(), **data}
                        datasource = DataSource(**attributes)
                        found = True
                    except NoDataFound:
                        found = False
                    except (ProviderError, DriverError, StatementError) as ex:
                        return self.error(
                            response={
                                "message": f"Error getting Datasource: {ds}",
                                "error": str(ex)
                            },
                            status=409
                        )
                if not found:
                    try:
                        try:
                            program_slug = data['program_slug']
                        except KeyError:
                            program_slug = 'navigator'
                        attributes = {
                            "name": data['name'],
                            "description": data['description'],
                            "credentials": driver.auth,
                            "params": driver.get_parameters(),
                            "driver": drvname,
                            "program_slug": program_slug,
                            # "drv": driver # TODO: serialized driver
                        }
                        datasource = DataSource(**attributes)
                    except ValueError as ex:
                        return self.error(
                            response={
                                "message": f'Invalid dataSource using {attributes!s}',
                                "error": str(ex),
                            },
                            status=406
                        )
                    except ValidationError as ex:
                        return self.error(
                            response={
                                "message": f'Invalid dataSource using {attributes!s}',
                                "payload": str(ex.payload),
                            },
                            exception=ex,
                            status=406
                        )
                ### Saving Datasource:
                try:
                    if found:
                        result = await datasource.update(_connection=conn)
                        status = 202
                    else:
                        result = await datasource.insert(_connection=conn)
                        status = 201
                    # if was inserted, then datasource will be updated:
                    try:
                        DATASOURCES[datasource.name] = driver
                    except (ValueError, AttributeError):
                        self._logger.warning(
                            f"We cannot update DATASOURCES list with {datasource.name} datasource."
                        )
                    return self.json_response(
                        response=result,
                        status=status
                    )
                except (ProviderError, DriverError, StatementError) as ex:
                    return self.error(
                        response={
                            "message": "Error insert/updating Datasource",
                            "error": str(ex)
                        },
                        status=409
                    )
        finally:
            await db.close()

    async def patch(self):
        """
        PATCH Method.
        description: updating partially info about a Datasource
        tags:
        - Datasource
        - datasources
        - Database connections
        consumes:
        - application/merge-patch+json
        produces:
        - application/json
        responses:
            "200":
                description: Existing Datasource was updated.
            "201":
                description: New Datasource was inserted
            "304":
                description: Datasource not modified, its currently the actual version
            "403":
                description: Forbidden Call
            "404":
                description: No Data was found
            "406":
                description: Query Error
            "409":
                description: Conflict, a constraint was violated
        """
        raise NotImplementedError(
            "Datasource patch method is not implemented Yet."
        )
