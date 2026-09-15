"""
QueryManager.

Managing query raws on Table Query Util.

GET /api/v1/management/queries supports paginated listing via
``page`` / ``page_size`` / ``sort`` / ``search`` query-string parameters —
see :mod:`querysource.handlers._pagination` and the FEAT-090 spec.
"""
# for aiohttp
from math import ceil

from asyncdb.exceptions import NoDataFound
from datamodel.exceptions import ValidationError
from navconfig.logging import logging
from pydantic import ValidationError as PydanticValidationError

from ..models import QueryModel
from ..repositories import DefinitionRepository
from ..tenant_errors import TenantError
from ..tenants import QueryIdentity, QueryStore, TenantRegistry
from ..types.validators import Entity

# Output
from ..utils.handlers import QueryView
from ._pagination import (
    PaginatedResponse,
    PaginationParams,
    build_count_sql,
    build_order_by,
    build_page_sql,
    build_where_clause,
)


class QueryManager(QueryView):
    _model: QueryModel = None

    # Default projection for the list branch when the caller does
    # not supply ``?fields=``. The single-slug / :meta / :insert
    # branches ignore ``default_fields``.
    default_fields = [
        "query_slug", "description", "conditions", "is_cached",
        "cache_refresh", "program_slug", "provider", "dwh",
        "created_at", "created_by", "updated_at", "updated_by"
    ]

    def post_init(self, *args, **kwargs):
        self._logger_name = 'QS.Manager'
        super().post_init(*args, **kwargs)

    def get_model(self, **kwargs):
        try:

            self._model = QueryModel(**kwargs)
            return self._model
        except Exception as err:
            print(err)

    def get_query_insert(self, query: QueryModel) -> str:
        ## TODO: add on-conflict logic
        sql = """INSERT INTO {schema}.{table} ({columns}) VALUES ({values});"""
        columns = []
        values = []
        fields = query.columns()
        for name, field in fields.items():
            val = getattr(query, field.name)
            _type = field.type
            try:
                _dbtype = field.db_type()
            except Exception:
                _dbtype = None
            value = Entity.toSQL(val, _type, dbtype=_dbtype)
            columns.append(name)
            values.append(value)
        values = ', '.join([Entity.quoteString(str(a), no_dblquoting=False) for a in values])
        return sql.format(
            schema=query.Meta.schema,
            table=query.Meta.name,
            columns=', '.join(columns),
            values=values
        )

    async def resolve_store(self, registry: TenantRegistry) -> QueryStore:
        """Resolve the store for this request using tenant selector parsing.

        Args:
            registry: Immutable tenant registry from QuerySource initialization

        Returns:
            The resolved QueryStore for this request.

        Raises:
            web.HTTPBadRequest: On invalid or conflicting tenant selectors.
        """
        from .tenant import resolve_request_store

        # Extract payload from request body if present
        payload = None
        try:
            payload = await self.json_data()
        except Exception:
            # No JSON body or parse error - ignore
            pass

        return resolve_request_store(self.request, registry, payload)

    def _strip_selectors(self, qp: dict) -> dict:
        """Remove tenant selector from query parameters before field/filter validation.

        Args:
            qp: Parsed query parameters

        Returns:
            Query parameters with tenant selector removed
        """
        qp = qp.copy()
        qp.pop('tenant', None)
        return qp

    async def _sync_definition_jobs(self, identity: QueryIdentity) -> bool:
        """Synchronize jobs only after the definition transaction commits.
        
        No scheduler => success; otherwise selected-owner register/remove; failure logs and returns False; callers set failure header.
        
        Args:
            identity: The mutated definition's immutable identity
            
        Returns:
            True if synchronization succeeded or no scheduler is active, False if synchronization failed
        """
        # No scheduler => success
        scheduler = self.request.app.get("qs_scheduler")
        if scheduler is None:
            return True
            
        try:
            # Synchronize only the selected owner's scheduled jobs
            await scheduler.register_slug(identity.slug, tenant=identity.store.schema if identity.store.contract == "tenant" else None)
            return True
        except Exception as exc:
            # Failure logs and returns False; callers set failure header
            self.logger.error(
                "Scheduler synchronization failed for slug '%s' in store %s.%s: %s",
                identity.slug, identity.store.schema, identity.store.table, exc
            )
            return False

    async def get(self):
        """
        get.
            summary: get a named query
        """
        params = self.get_arguments()
        qp = self.query_parameters(self.request)
        args = self.match_parameters(self.request)
        # can parse filter-based searchs
        try:
            meta = args['meta']
        except (TypeError, KeyError):
            meta = ''
        try:
            query_slug = params['slug']
            try:
                query_slug, meta = query_slug.split(':')
            except (TypeError, AttributeError, ValueError):
                pass
        except KeyError:
            query_slug = None

        # Resolve store once before any field/filter/:meta/:insert decision
        # (AC-2/AC-4: get, pagination, :meta and :insert all route through
        # the repository, which is per-store) — but ONLY when the tenant
        # registry/repository were actually published by QuerySource.setup()
        # + qs_start (TASK-720). An app that has not adopted the tenant
        # feature yet (e.g. tests/handlers/conftest.py's fixture, which
        # only sets app["qs_connection"]) must keep working exactly as
        # before: AC-4 "preserve legacy metadata/export/pagination
        # conventions... existing regression tests".
        registry = self.request.app.get('qs_tenant_registry')
        repo: DefinitionRepository | None = self.request.app.get('qs_definition_repository')
        store: QueryStore | None = None
        if registry is not None and repo is not None:
            store = await self.resolve_store(registry)

        # Remove selectors before field/filter validation
        qp = self._strip_selectors(qp)

        try:
            if meta == ':meta' and not query_slug:
                # Route-level :meta (no slug attached): schema for the
                # resolved store's persistence model, or the legacy
                # full-model schema when tenants are not wired up.
                response = repo.schema(store) if repo is not None else QueryModel.schema(as_dict=True)
                return self.json_response(response=response)
            if query_slug:
                # Single-slug branch: get, :meta, or :insert.
                if repo is not None:
                    # Routed through the repository, which already
                    # preserves legacy vs tenant program_slug conventions
                    # (TASK-718 DefinitionRepository._runtime_model).
                    identity = QueryIdentity(store=store, slug=query_slug)
                    if meta == 'insert':
                        sentence = await repo.export_insert(identity)
                        self.logger.debug('INSERT > %s', sentence)
                        response = {
                            "slug": query_slug,
                            "sql": sentence
                        }
                        return self.json_response(response)
                    if meta == ':meta':
                        response = repo.schema(store)
                        return self.json_response(response=response)
                    loaded = await repo.get(identity)
                    return self.json_response(loaded.runtime)
                # Legacy fallback: tenant registry/repository not wired up
                # for this app — original direct-ORM behavior, unchanged.
                db = self.request.app['qs_connection']
                async with await db.acquire() as conn:
                    query = await QueryModel.get(query_slug=query_slug, _connection=conn)
                    if meta == 'insert':
                        sentence = self.get_query_insert(query)
                        self.logger.debug('INSERT > %s', sentence)
                        response = {
                            "slug": query_slug,
                            "sql": sentence
                        }
                        return self.json_response(response)
                    if meta == ':meta':
                        response = QueryModel.schema(as_dict=True)
                        return self.json_response(response=response)
                    return self.json_response(query)
            # List-pagination branch: no slug, no :meta, no :insert.
            # ``qp['fields']`` (if present) is parsed + allowlist-validated
            # by ``PaginationParams.from_query_string`` inside
            # ``_paginate_list``. Remaining qp keys flow as equality filters
            # and are rejected (400) by ``build_where_clause`` when they
            # are not in the QueryModel allowlist.
            self.logger.debug('QP %s', qp)
            return await self._paginate_list(
                qp, {"fields": self.default_fields}, store=store
            )
        except TenantError as err:
            if err.error_code == "query_not_found":
                headers = {
                    'X-STATUS': 'EMPTY',
                    'X-ERROR': str(err),
                    'X-MESSAGE': f'Query Source {query_slug} not Found'
                }
                return self.no_content(headers=headers)
            return self.error(
                reason=str(err),
                exception=err,
                status=err.code
            )
        except NoDataFound as err:
            headers = {
                'X-STATUS': 'EMPTY',
                'X-ERROR': str(err),
                'X-MESSAGE': f'Query Source {query_slug} not Found'
            }
            return self.no_content(headers=headers)
        except Exception as err:
            return self.error(
                reason=f"Error getting Query Slug: {err}",
                exception=err
            )

    async def _paginate_list(self, qp: dict, default_args: dict, *, store: QueryStore | None = None):
        """Paginated list fetch for ``GET /api/v1/management/queries``.

        Implements the FEAT-090 spec § 3 Module 3. Parses pagination /
        sort / search params from ``qp``, builds a WHERE / ORDER BY / LIMIT
        / OFFSET SQL pair via :mod:`._pagination`, and runs them through the
        shared ``qs_connection`` pool.

        Args:
            qp: Parsed query-string parameters (already stripped of
                ``fields`` by the caller). Keys not consumed by
                :class:`PaginationParams` are treated as equality filters.
            default_args: Default kwargs pre-built by ``get()`` — notably
                ``default_args['fields']`` holds the projection list used
                when the caller does not supply ``?fields=``.
            store: Optional QueryStore for tenant-aware listing. If not
                provided, uses the legacy default store.

        Returns:
            aiohttp response:
                * ``200`` with ``{data, meta}`` envelope + pagination
                  headers on non-empty results.
                * ``204`` with pagination headers when ``total == 0``.
                * ``400`` on validation errors.
                * ``500`` on unexpected DB errors.
        """
        try:
            params = PaginationParams.from_query_string(qp)
        except (ValueError, PydanticValidationError) as err:
            return self.error(
                response={"message": f"Invalid pagination params: {err}"},
                status=400,
            )

        # AC-3: tenant-contract stores reject program_slug in sort/search/
        # fields — a request-local check scoped to this store, never a
        # module-global mutation of SORTABLE_COLUMNS/SEARCHABLE_COLUMNS
        # (which must keep serving legacy program_slug listing unchanged).
        if store is not None and store.contract == "tenant" and (
            params.sort_field == "program_slug"
            or (params.fields and "program_slug" in params.fields)
            or "program_slug" in qp
        ):
            return self.error(
                response={"message": "program_slug is not a queryable field for this store"},
                status=400,
            )

        # Pagination-reserved keys must not leak into the WHERE clause.
        reserved = {"page", "page_size", "sort", "search", "fields"}
        extra_filters = {k: v for k, v in qp.items() if k not in reserved}

        # Resolve the projection list. ``params.fields`` was already
        # allowlist-validated by the Pydantic model; fall back to the
        # defaults computed by ``get()``.
        fields = (
            params.fields
            or default_args.get("fields")
            or list(QueryModel.columns(QueryModel).keys())
        )

        # Use store-specific schema/table if provided, otherwise use legacy defaults
        if store is None:
            schema = QueryModel.Meta.schema
            table = QueryModel.Meta.name
        else:
            schema = store.schema
            table = store.table

        try:
            # Tenant-contract stores never persist program_slug (see
            # docs/PER_TENANT_QUERIES.md "Persistence versus runtime
            # shape") — excluded from the search OR-group here so a
            # tenant-store ?search= request never references a nonexistent
            # column. This is a request-local exclusion (never a mutation
            # of the shared SEARCHABLE_COLUMNS constant, which must keep
            # serving legacy search unchanged). The explicit sort/fields/
            # equality-filter rejection above stays as-is — this only
            # closes the gap for ?search=, which was previously
            # unconditionally SQL'd against program_slug for every store.
            exclude_search_columns = (
                frozenset({"program_slug"})
                if store is not None and store.contract == "tenant"
                else frozenset()
            )
            where = build_where_clause(
                params, extra_filters, exclude_search_columns=exclude_search_columns
            )
            order_by = build_order_by(params)
            count_sql = build_count_sql(schema, table, where)
            page_sql = build_page_sql(
                schema,
                table,
                fields,
                where,
                order_by,
                limit=params.page_size,
                offset=params.offset,
            )
        except ValueError as err:
            return self.error(
                response={"message": f"Invalid filter/sort: {err}"},
                status=400,
            )

        db = self.request.app['qs_connection']
        try:
            async with await db.acquire() as conn:
                total = await conn.fetchval(count_sql) or 0
                total = int(total)
                total_pages = ceil(total / params.page_size) if total else 0
                headers = {
                    "X-Total-Count": str(total),
                    "X-Page": str(params.page),
                    "X-Page-Size": str(params.page_size),
                    "X-Total-Pages": str(total_pages),
                }
                if total == 0:
                    return self.no_content(headers=headers)
                # ``fetch_all`` is the correct raw-SQL fetcher on the
                # asyncdb pg driver (see
                # ``asyncdb/drivers/pg.py:893``). ``conn.fetch(number=1)``
                # is a cursor-advance helper that expects an integer, not
                # a SQL string. ``fetch_all`` may return ``None`` on empty
                # results — TOCTOU-safe ``(rows or [])`` below.
                rows = await conn.fetch_all(page_sql)
                data = [dict(row) for row in (rows or [])]
                response = PaginatedResponse(
                    data=data,
                    meta={
                        "page": params.page,
                        "page_size": params.page_size,
                        "total": total,
                        "total_pages": total_pages,
                    },
                ).model_dump()
                return self.json_response(response, headers=headers)
        except Exception as err:  # pragma: no cover - defensive catch-all
            self.logger.exception(
                "Error paginating Query Slug list: %s", err
            )
            return self.error(
                reason=f"Error paginating Query Slug list: {err}",
                exception=err,
            )

    async def patch(self):
        """Resolve selected store; call repository patch; preserve statuses and strip routing metadata."""
        params = self.get_arguments()
        try:
            query_slug = params['slug']
        except KeyError:
            headers = {
                'X-STATUS': 'Error',
                'X-MESSAGE': 'Query Slug is missing'
            }
            return self.error(
                response={"message": 'Query Slug is missing'},
                headers=headers
            )
        # try to got post data
        data = await self.json_data()
        if not data:
            return self.error(
                response={"message": 'Missing Data for change Query Slug'},
            )
        
        # Resolve store once before any field/filter/:meta/:insert decision
        registry = self.request.app.get('qs_tenant_registry')
        repo = self.request.app.get('qs_definition_repository')
        store = None
        if registry is not None and repo is not None:
            try:
                store = await self.resolve_store(registry)
            except Exception as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=getattr(err, 'status', 400)
                )
            
            # Remove selectors before field/filter validation
            data = self._strip_selectors(data)
            
            # Validate slug agreement between path and body
            if 'query_slug' in data and data['query_slug'] != query_slug:
                return self.error(
                    response={"message": 'Path and body slug must agree'},
                    status=400
                )
            
            # Call repository patch
            try:
                identity = QueryIdentity(store=store, slug=query_slug)
                result = await repo.patch(identity, data)
                
                # Sync definition jobs if scheduler is active
                sync_success = await self._sync_definition_jobs(identity)
                if not sync_success:
                    # Report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback
                    headers = {"X-QS-Scheduler-Sync": "failed"}
                    return self.json_response(result, headers=headers)
                
                return self.json_response(result)
            except TenantError as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=err.code
                )
            except Exception as err:
                self.logger.error(f'Error patching query slug: {err}')
                return self.error(
                    response={"message": f'Unprocessable partial Updating: {query_slug}'},
                    exception=err,
                    status=422
                )
        
        # Legacy fallback: tenant registry/repository not wired up
        parameters = {
            "query_slug": query_slug
        }
        # trying to update the model
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                qry = self.get_model(**parameters)

                slug = await qry.get(_connection=conn, **parameters)
                for k, v in data.items():
                    setattr(slug, k, v)
                update = await slug.update(_connection=conn)
            if update:
                return self.json_response(update)
            else:
                return self.error(
                    response=f'Resource not found: {query_slug}',
                    status=404
                )
        except ValidationError as ex:
            print('ERR ', ex)
            return self.error(
                response={"message": f'Invalid Slug Data: {query_slug}: {ex}'},
                exception=str(ex),
                status=400
            )
        except NoDataFound as err:
            headers = {
                'X-STATUS': 'EMPTY',
                'X-MESSAGE': f'Query Source {query_slug} not Found'
            }
            return self.error(
                response={"message": f'Query Slug not found: {query_slug}:'},
                exception=err,
                status=404,
                headers=headers
            )
        except Exception as err:
            print('EXEPT ', err)
            return self.error(
                response={"message": f'Unprocessable partial Updating: {query_slug}'},
                exception=err,
                status=422
            )

    async def delete(self):
        """"
        delete.
           summary: delete a query slug or request a redis cache delete
        """
        params = self.get_arguments()
        try:
            query_slug = params['slug']
        except KeyError as err:
            headers = {
                'X-STATUS': 'Error',
                'X-MESSAGE': 'Query slug Name is missing'
            }
            return self.error(
                response={"message": 'Query slug Name is missing'},
                exception=err,
                headers=headers
            )
        
        # Resolve store once before any field/filter/:meta/:insert decision
        registry = self.request.app.get('qs_tenant_registry')
        repo = self.request.app.get('qs_definition_repository')
        store = None
        if registry is not None and repo is not None:
            try:
                store = await self.resolve_store(registry)
            except Exception as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=getattr(err, 'status', 400)
                )
            
            # Call repository delete
            try:
                identity = QueryIdentity(store=store, slug=query_slug)
                result = await repo.delete(identity)
                
                # Sync definition jobs if scheduler is active
                sync_success = await self._sync_definition_jobs(identity)
                if not sync_success:
                    # Report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback
                    headers = {"X-QS-Scheduler-Sync": "failed"}
                    return self.json_response(result, headers=headers)
                
                msg = {
                    "result": result
                }
                headers = {
                    'X-STATUS': 'OK',
                    'X-MESSAGE': f'Query Source {query_slug} was deleted'
                }
                return self.json_response(
                    msg,
                    headers=headers,
                    status=202
                )
            except TenantError as err:
                if err.error_code == "query_not_found":
                    headers = {
                        'X-STATUS': 'EMPTY',
                        'X-MESSAGE': f'Query Source {query_slug} not Found'
                    }
                    return self.error(
                        response={"message": f'Query Slug not found: {query_slug}:'},
                        exception=err,
                        status=404,
                        headers=headers
                    )
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=err.code
                )
            except Exception as err:
                self.logger.error(f'Error deleting query slug: {err}')
                return self.critical(
                    exception=err,
                    traceback=''
                )
        
        parameters = {
            "query_slug": query_slug
        }
        # trying to update the model
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                slug = await QueryModel.get(_connection=conn, **parameters)
                result = await slug.delete(_connection=conn)
            if result:
                msg = {
                    "result": result
                }
                headers = {
                    'X-STATUS': 'OK',
                    'X-MESSAGE': f'Query Source {query_slug} was deleted'
                }
                return self.json_response(
                    msg,
                    headers=headers,
                    status=202
                )
            else:
                headers = {
                    'X-STATUS': 'Error',
                    'X-MESSAGE': f'Query Source {query_slug} Delete error'
                }
                return self.error(
                    response={"message": f'Query Source {query_slug} was not deleted'},
                    headers=headers
                )
        except ValidationError as ex:
            print('ERR ', ex)
            return self.error(
                response={"message": f'Invalid Slug Data: {query_slug}: {ex}'},
                exception=str(ex),
                status=400
            )
        except NoDataFound as err:
            headers = {
                'X-STATUS': 'EMPTY',
                'X-MESSAGE': f'Query Source {query_slug} not Found'
            }
            return self.error(
                response={"message": f'Query Slug not found: {query_slug}:'},
                exception=err,
                status=404,
                headers=headers
            )
        except Exception as err:
            print('ERROR ', err)
            return self.critical(
                exception=err,
                traceback=''
            )

    async def put(self):
        """"
        put.
           summary: insert (or modify) a query slug
        """
        try:
            data = await self.json_data()
        except (TypeError, AttributeError):
            return self.error(
                response={"message": "Error loading POST data"},
                status=406
            )
        if not data:
            return self.error(
                response={"message": "Cannot Insert a row without JSON post data"},
                status=406
            )
        if 'query_slug' not in data:
            headers = {
                'X-STATUS': 'Error',
                'X-MESSAGE': 'Query Name (slug) is missing'
            }
            return self.error(
                response={"message": 'Query Name (slug) is missing'},
                headers=headers
            )
        
        # Resolve store once before any field/filter/:meta/:insert decision
        registry = self.request.app.get('qs_tenant_registry')
        repo = self.request.app.get('qs_definition_repository')
        store = None
        if registry is not None and repo is not None:
            try:
                store = await self.resolve_store(registry)
            except Exception as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=getattr(err, 'status', 400)
                )
            
            # Remove selectors before field/filter validation
            data = self._strip_selectors(data)
            
            # Call repository upsert
            try:
                identity = QueryIdentity(store=store, slug=data['query_slug'])
                result, is_created = await repo.upsert(identity, data)
                
                # Sync definition jobs if scheduler is active
                sync_success = await self._sync_definition_jobs(identity)
                if not sync_success:
                    # Report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback
                    headers = {"X-QS-Scheduler-Sync": "failed"}
                    return self.json_response(result, headers=headers)
                
                status = 201 if is_created else 202
                return self.json_response(result, status=status)
            except TenantError as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=err.code
                )
            except Exception as err:
                self.logger.error(f'Error upserting query slug: {err}')
                return self.critical(
                    response={"message": f"Error creating/updating an slug: {data}"},
                    exception=err
                )
        
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                # first: try to get if Slug exists:
                try:
                    qry = self.get_model(**data)  # pylint: disable=E1102
                except ValidationError as ex:
                    error = {
                        "error": "Unable to insert Query Slug info",
                        "payload": ex.payload,
                    }
                    return self.error(
                        reason=error,
                        status=406
                    )
                result = None
                st = 204
                try:
                    ## try to get an existing slug, or insert if none
                    slug = await QueryModel.get(query_slug=qry.query_slug, _connection=conn)
                    if slug:
                        ## try to update slug:
                        for k, v in data.items():
                            setattr(slug, k, v)
                        result = await slug.update(_connection=conn)
                        st = 202
                except NoDataFound:
                    result = await qry.insert(_connection=conn)
                    st = 201
                # Saving Slug in redis cache:
                return self.json_response(result, status=st)
        except Exception as err:
            print('ERROR ', err)
            return self.critical(
                response={"message": f"Error creating/updating an slug: {data}"},
                exception=err
            )

    async def post(self):
        """Resolve selected store; call repository upsert; preserve statuses and strip routing metadata."""
        params = self.get_arguments()
        data = await self.json_data()
        slug = None
        try:
            slug = {
                "query_slug": params['slug']
            }
        except KeyError:
            try:
                slug = {
                    "query_slug": data['query_slug']
                }
            except KeyError:
                pass
        if not slug:
            return self.error(
                response={"message": "Query Name (slug) missing in payload or URL"},
                status=401
            )
        if not data:
            return self.error(
                response={"message": "Cannot Update row without JSON post data"},
                status=406
            )
        
        # Resolve store once before any field/filter/:meta/:insert decision
        registry = self.request.app.get('qs_tenant_registry')
        repo = self.request.app.get('qs_definition_repository')
        store = None
        if registry is not None and repo is not None:
            try:
                store = await self.resolve_store(registry)
            except Exception as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=getattr(err, 'status', 400)
                )
            
            # Remove selectors before field/filter validation
            data = self._strip_selectors(data)
            
            # Validate slug agreement between path and body
            if 'query_slug' in data and data['query_slug'] != slug['query_slug']:
                return self.error(
                    response={"message": 'Path and body slug must agree'},
                    status=400
                )
            
            # Call repository upsert
            try:
                identity = QueryIdentity(store=store, slug=slug['query_slug'])
                result, is_created = await repo.upsert(identity, data)
                
                # Sync definition jobs if scheduler is active
                sync_success = await self._sync_definition_jobs(identity)
                if not sync_success:
                    # Report sync failure via X-QS-Scheduler-Sync: failed and logs, without claiming rollback
                    headers = {"X-QS-Scheduler-Sync": "failed"}
                    return self.json_response(result, headers=headers)
                
                status = 201 if is_created else 202
                return self.json_response(result, status=status)
            except TenantError as err:
                return self.error(
                    reason=str(err),
                    exception=err,
                    status=err.code
                )
            except Exception as err:
                self.logger.error(f'Error upserting query slug: {err}')
                return self.critical(
                    response={"message": f"Error creating/updating an slug: {data}"},
                    exception=err
                )
        
        # Legacy fallback: tenant registry/repository not wired up
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                ### first, validate data:
                try:
                    qry = self.get_model(**data)  # pylint: disable=E1102
                except ValidationError as ex:
                    error = {
                        "error": "Unable to Update Query Slug info",
                        "payload": ex.payload,
                    }
                    return self.error(
                        reason=error,
                        status=406
                    )
                try:
                    slug_obj = await QueryModel.get(_connection=conn, **slug)
                    ## try to update slug:
                    for k, v in data.items():
                        setattr(slug_obj, k, v)
                    result = await slug_obj.update(_connection=conn)
                    return self.json_response(result, status=202)
                except NoDataFound:
                    logging.warning(f"No Query slug was found: {slug}")
                    result = await qry.insert(_connection=conn)
                    return self.json_response(result, status=201)
        except Exception as err:
            print('ERROR ', err)
            return self.critical(
                response={"message": f"Error creating/updating an slug: {data}"},
                exception=err
            )
