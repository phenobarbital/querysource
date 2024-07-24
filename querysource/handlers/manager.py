"""
QueryManager.

Managing query raws on Table Query Util.
"""
# for aiohttp
from aiohttp.web import View
from navconfig.logging import logging
from datamodel.exceptions import ValidationError
from asyncdb.exceptions import (
    NoDataFound
)
from ..models import QueryModel
# Output
from ..utils.handlers import QueryView
from ..types.validators import Entity

class QueryManager(QueryView):
    _model: QueryModel = None

    def post_init(self, *args, **kwargs):
        self._logger_name = 'QS.Manager'
        super(QueryManager, self).post_init(*args, **kwargs)

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
            if meta == ':meta':
                # returning JSON schema of Model:
                response = QueryModel.schema(as_dict=True)
                return self.json_response(response=response)
        except (TypeError, KeyError):
            pass
        try:
            query_slug = params['slug']
            try:
                query_slug, meta = query_slug.split(':')
            except (TypeError, AttributeError, ValueError):
                pass
        except KeyError:
            query_slug = None
        try:
            if 'fields' in qp:
                args = {
                    "fields": qp['fields']
                }
            else:
                args = {
                    "fields": [
                        "query_slug", "description", "conditions", "is_cached",
                        "cache_refresh", "program_slug", "provider", "dwh",
                        "created_at", "created_by", "updated_at", "updated_by"
                    ]
                }
            try:
                del qp['fields']
            except KeyError:
                pass
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                QueryModel.Meta.connection = conn
                if query_slug:
                    query = await QueryModel.get(**{"query_slug": query_slug})
                    if meta == 'insert':
                        # converting query into an INSERT INTO sentence
                        sentence = self.get_query_insert(query)
                        print('INSERT > ', sentence)
                        response = {
                            "slug": query_slug,
                            "sql": sentence
                        }
                        return self.json_response(response)
                elif len(qp) > 0:
                    args = {**args, **qp}
                    print('ARGS ', args)
                    query = await QueryModel.filter(**qp)
                else:
                    query = await QueryModel.all(**args)
                    query = [row.dict() for row in query]
                return self.json_response(query)
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

    async def patch(self):
        """
        patch.
            summary: return the metadata from a query slug or, if we got post
            realizes a partially atomic updated of the query.
        """
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
        parameters = {
            "query_slug": query_slug
        }
        # trying to update the model
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                qry = self.get_model(**parameters)

                qry.Meta.connection = conn
                slug = await qry.get(**parameters)
                for k, v in data.items():
                    setattr(slug, k, v)
                update = await slug.update()
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
        parameters = {
            "query_slug": query_slug
        }
        # trying to update the model
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                QueryModel.Meta.connection = conn
                slug = await QueryModel.get(**parameters)
                result = await slug.delete()
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
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                QueryModel.Meta.connection = conn
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
                    slug = await QueryModel.get(query_slug=qry.query_slug)
                    if slug:
                        ## try to update slug:
                        for k, v in data.items():
                            setattr(slug, k, v)
                        result = await slug.update()
                        st = 202
                except NoDataFound:
                    result = await qry.insert()
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
        """
        post.
            summary: update (or create) a query slug
        """
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
        try:
            db = self.request.app['qs_connection']
            async with await db.acquire() as conn:
                QueryModel.Meta.connection = conn
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
                    slug = await QueryModel.get(**slug)
                    ## try to update slug:
                    for k, v in data.items():
                        setattr(slug, k, v)
                    result = await slug.update()
                    return self.json_response(result, status=202)
                except NoDataFound:
                    logging.warning(f"No Query slug was found: {slug}")
                    result = await qry.insert()
                    return self.json_response(result, status=201)
        except Exception as err:
            print('ERROR ', err)
            return self.critical(
                response={"message": f"Error creating/updating an slug: {data}"},
                exception=err
            )
