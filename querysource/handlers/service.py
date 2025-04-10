"""
QueryResource.

Handler to accessing querysource objects from API.
"""
from typing import Optional
import contextlib
from datetime import datetime
# for aiohttp
from aiohttp import web
from asyncdb.exceptions import (
    ProviderError,
    ConnectionTimeout
)  # QuerySource Query, Executor, etc
# Output
from ..outputs import DataOutput
from ..types import graph_ouputs, mime_supported
from ..exceptions import (
    ParserError,
    QueryException,
    SlugNotFound,
    DriverError,
)
from ..conf import (
    CSV_DEFAULT_DELIMITER,
    CSV_DEFAULT_QUOTING
)
from .abstract import AbstractHandler


class QueryService(AbstractHandler):

    async def run_queries(self, request: web.Request) -> web.Response:
        """run_queries

        Description: simple user interface to making queries directly to QuerySource.
        tags:
        - QuerySource
        produces:
        - application/json

        Returns:
            web.Response: Data retrieved by Query.
        """
        app = request.app
        tpl = app['templating']
        return await tpl.view(
            filename='querysource.html',
            content_type='text/html'
        )

    async def run_query(self, request: web.Request) -> web.Response:
        """run_query

        Description: Run arbitrary queries with parameters and return response.
        tags:
        - QuerySource
        produces:
        - application/json

        Returns:
            web.Response: Data retrieved by Query.
        """
        options = {}
        params = self.query_parameters(request)
        _format: str = 'json'  # TODO: adding support for outputs.
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            options = {}
        queryformat = self.format(request, params, _format)
        try:
            query = options['query']
        except KeyError as ex:
            raise self.Error(
                message="Option *query* was not found in payload.",
                code=400
            ) from ex
        try:
            driver = options['driver']
            del options['driver']
        except KeyError:
            driver = 'db'
        if not options:
            options = {}
        output_args = {
            "filename": None,
            "download": False,
            "writer_options": {},
        }
        # get conditions
        conditions = {**options, **params}
        self.logger.debug(f'Test Query: {query}, format: {queryformat}, conditions: {conditions}')
        if query := await self.get_source(
                request,
                slug=None,
                query=query,
                conditions=conditions,
                driver=driver
        ):
            try:
                await query.build_provider()
            except ParserError as err:
                raise self.Error(
                    message="Error parsing Query",
                    exception=err
                )
            except (ProviderError, DriverError) as err:
                raise self.Error(
                    message="Connection Error",
                    exception=err
                )
            # query columns
            try:
                output = DataOutput(request, query=query, ctype=queryformat, slug=None, **output_args)
                return await output.response()
            except (ProviderError, DriverError) as err:
                raise self.Error(
                    message="DataOutput Error",
                    exception=err,
                    code=402
                )
            except web.HTTPException:
                raise
            except (QueryException, Exception) as ex:
                raise self.Except(
                    message="Error on Query",
                    exception=ex
                ) from ex
        else:
            raise self.Error(
                message="Unable to get Provider for Query"
            )

    async def query(self, request):
        """
        ---
        description: Allow make Database Queries to Navigator or any external database or source.
        summary: get a Query by Slug (Named Queries)
        tags:
        - QuerySource
        produces:
        - application/json + application/xml + application/html
        parameters:
        - name: slug
            description: slug Id of Query
            in: path
            required: true
            type: string
        responses:
            "200":
                description: returns valid data
            "204":
                description: No Data was found
            "403":
                description: Forbidden Execution
            "404":
                description: Query with name doesn't exists
            "405":
                description: invalid HTTP Method
            "406":
                description: Query Error
        """
        options = {}
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        writer_options = {}
        _format: Optional[str] = None
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            options = {}
        try:
            slug: str = args['slug']
            try:
                slug, _format = slug.split(':')
            except ValueError:
                pass
            del args['slug']
        except KeyError:
            slug: str = None
        except Exception as err:  # pylint: disable=W0703
            return self.NotFound(
                message="QS: Error with parameters.", exception=err
            )
        # get the format: returns a valid MIME-Type string to use in DataOutput
        try:
            _format = params['queryformat']
            del params['queryformat']
        except KeyError:
            pass
        # extracting params from FORMAT:
        try:
            _format, tpl = _format.split('=')
        except (AttributeError, ValueError):
            tpl = None
        if tpl:
            try:
                report = options['_report_options']
            except (TypeError, KeyError):
                report = {}
            writer_options = {
                "template": tpl,
                **report
            }
        queryformat = self.format(request, params, _format)
        ## will be a downloadable resource
        try:
            download = params['_download']
            del params['_download']
        except KeyError:
            download = False
        ## default filename for export:
        try:
            filename = params['_filename']
            del params['_filename']
        except KeyError:
            filename = None
        # Many other parsing options:
        if _format == 'csv':
            try:
                writer_options = options['_csv_options']
                del options['_csv_options']
            except (TypeError, KeyError):  # default options:
                writer_options = {
                    "delimiter": CSV_DEFAULT_DELIMITER,
                    "quoting": CSV_DEFAULT_QUOTING
                }
        else:
            try:
                writer_options = options['_output_options']
                del options['_output_options']
            except (TypeError, KeyError):
                pass
            try:
                del options['_csv_options']
            except (TypeError, KeyError):
                pass
        if queryformat in graph_ouputs:
            ## TODO: add default options
            writer_options = {}
        try:
            writer_options = options['_graph_options']
            del options['_graph_options']
        except (TypeError, KeyError):  # default options:
            pass
        output_args = {
            "filename": filename,
            "download": download,
            "writer_options": writer_options,
        }
        if not options:
            options = {}
        # get conditions
        conditions = {**options, **params}
        self.logger.debug(
            f'Slug: {slug}, format: {queryformat}, conditions: {conditions}'
        )
        try:
            if query := await self.get_source(request, slug, conditions, driver=args):
                try:
                    await query.build_provider()
                except SlugNotFound as err:
                    raise self.Error(
                        message=f"Slug Not Found: {slug}",
                        exception=err,
                        code=400
                    )
                except ParserError as err:
                    raise self.Error(
                        message=f"Error parsing Query Slug {slug}",
                        exception=err
                    )
                except (ProviderError, DriverError) as err:
                    raise self.Error(
                        message="Connection Error",
                        exception=err
                    )
                except Exception as ex:
                    raise self.Except(
                        message="Unknown Error on Query",
                        exception=ex
                    ) from ex
                # query columns
                if not queryformat:
                    if ctype := query.accepts():
                        queryformat = mime_supported[ctype]
                try:
                    output = DataOutput(
                        request,
                        query=query,
                        ctype=queryformat,
                        slug=slug,
                        **output_args
                    )
                    return await output.response()
                except ConnectionTimeout as err:
                    raise DriverError(
                        f"Connection Timeout: {err}"
                    ) from err
                except (ProviderError, DriverError) as err:
                    raise self.Error(
                        message="DataOutput Error",
                        exception=err,
                        code=402
                    )
                except (QueryException, Exception) as ex:
                    raise self.Except(
                        message=f"Error on Query: {slug}",
                        exception=ex
                    ) from ex
            else:
                raise self.Error(
                    message=f"Unable to get Provider for slug: {slug}"
                )
        except web.HTTPException:
            raise
        except (ProviderError, DriverError) as err:
            raise self.Error(
                message='Query Failed',
                exception=err
            )
        except (QueryException, Exception) as ex:
            raise self.Except(
                message="Uncaught Error on Query",
                exception=ex
            ) from ex

    async def get_columns(self, request):
        """
        Return the columns associated to a Named Query.
        """
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            options = {}
        try:
            slug: str = args.pop('slug')
            with contextlib.suppress(ValueError):
                slug, _ = slug.split(':')
        except KeyError:
            slug: str = None
        if not params:
            params = {}
        if not options:
            options = {}
        conditions = {**options, **params}
        self.logger.debug(
            f'Slug: {slug}, format: json, conditions: {conditions}'
        )
        try:
            if query := await self.get_source(request, slug, conditions, driver=args):
                try:
                    await query.build_provider()
                except SlugNotFound as err:
                    response_obj = {
                        'status': 'empty',
                        'message': err.message
                    }
                    return self.error(response=response_obj, status=404)
                except ParserError as err:
                    return self.Error(
                        message=f"Error parsing Query Slug {slug}",
                        exception=err
                    )
                except (ProviderError, DriverError) as err:
                    return self.Error(
                        message="Connection Error",
                        exception=err
                    )
                # query columns
                try:
                    columns = await query.columns()
                    if not columns:
                        # using the *columns* attribute in definition:
                        if definition := query.get_definition():
                            columns = definition.get('attributes', {}).get('columns', [])
                    if not columns:
                        columns = []
                    headers = {
                        "X-Columns": f"{columns!r}",
                        "X-Slug": str(slug),
                    }
                    if not columns:
                        headers['X-Message'] = "No Columns found"
                    if queryformat := query.accepts():
                        queryformat = mime_supported[queryformat]
                    if queryformat:
                        headers['Content-Type'] = queryformat
                    return self.no_content(headers=headers)
                except (ProviderError, DriverError, QueryException) as err:
                    return self.Error(
                        message="Columns Error",
                        exception=err,
                        code=402
                    )
            else:
                return self.Error(
                    message=f"Unable to get Provider for slug: {slug}"
                )
        except (ProviderError, DriverError) as err:
            return self.Error(
                message='Error on query Columns',
                exception=err
            )
        except (QueryException, Exception) as ex:
            raise self.Except(
                message="Unknown Error on Column Fn",
                exception=ex
            ) from ex

    async def columns(self, request):
        """
        ---
        description: Extract Columns associated to a Named Query
        summary: get columns of a Query by Slug (Named Queries)
        tags:
        - QuerySource
        produces:
        - application/json
        parameters:
        - name: slug
          description: slug Id of Query
          in: path
          required: true
          type: string
        responses:
            "200":
                description: returns valid data
            "204":
                description: No Data was found
            "403":
                description: Forbidden Execution
            "404":
                description: Query with name doesn't exists
            "405":
                description: invalid HTTP Method
            "406":
                description: Query Error
        """
        options = {}
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            options = {}
        try:
            slug: str = args['slug']
            _format: str = 'json'
            try:
                slug, _format = slug.split(':')
            except ValueError:
                pass
            del args['slug']
        except KeyError:
            slug: str = None
            _format: str = 'json'
        except Exception as err:  # pylint: disable=W0703
            return self.NotFound(message="QS: Error with parameters.", exception=err)
        # get the format: returns a valid MIME-Type string to use in DataOutput
        queryformat = self.format(request, params, _format)
        ## will be a downloadable resource
        try:
            del params['_download']
        except KeyError:
            pass
        ## default filename for export:
        try:
            del params['_filename']
        except KeyError:
            pass
        # Many other parsing options:
        try:
            del options['_csv_options']
        except (TypeError, KeyError):  # default options:
            pass
        if not options:
            options = {}
        # get conditions
        conditions = {**options, **params}
        self.logger.debug(f'Slug: {slug}, format: {queryformat}, conditions: {conditions}')
        try:
            if query := await self.get_source(request, slug, conditions, driver=args):
                try:
                    await query.build_provider()
                except SlugNotFound as err:
                    response_obj = {
                        'status': 'empty',
                        'message': err.message
                    }
                    return self.error(response=response_obj, status=404)
                except ParserError as err:
                    return self.Error(
                        message=f"Error parsing Query Slug {slug}",
                        exception=err
                    )
                except (ProviderError, DriverError) as err:
                    return self.Error(
                        message="Connection Error",
                        exception=err
                    )
                # query columns
                try:
                    columns = await query.columns()
                    return self.json_response(columns, status=200)
                except (ProviderError, DriverError, QueryException) as err:
                    return self.Error(
                        message="Columns Error",
                        exception=err,
                        code=402
                    )
            else:
                return self.Error(
                    message=f"Unable to get Provider for slug: {slug}"
                )
        except (ProviderError, DriverError) as err:
            return self.Error(
                message='Error on query Columns',
                exception=err
            )
        except (QueryException, Exception) as ex:
            raise self.Except(
                message="Unknown Error on Column Fn",
                exception=ex
            ) from ex

    async def test_slug(self, request: web.Request) -> web.StreamResponse:
        """test_slug.

        description: Test an existing Slug (returning the query created by Source)
        summary: get a Query by Slug (Named Queries)
        tags:
        - QuerySource
        produces:
        - application/json
        parameters:
        - name: slug
          description: slug Id of Query
          in: path
          required: true
          type: string
        responses:
            "200":
                description: returns valid data
            "204":
                description: No Data was found
            "403":
                description: Forbidden Execution
            "404":
                description: Query with name doesn't exists
            "405":
                description: invalid HTTP Method
            "406":
                description: Query Error
        """
        options = {}
        started = datetime.now()
        params = self.query_parameters(request)
        args = self.match_parameters(request)
        works = True
        ignore_query = 0
        execution = None
        try:
            ignore_query = params['ignore_query']
            del params['ignore_query']
        except KeyError:
            ignore_query = False
        try:
            options = await self.json_data(request)
        except (TypeError, ValueError):
            pass
        if not options:
            options = {}
        try:
            slug: str = args['slug']
            _format: str = 'json'
            try:
                slug, _format = slug.split(':')
            except ValueError:
                pass
            del args['slug']
        except KeyError:
            slug: str = None
            _format: str = 'json'
        except Exception as err:  # pylint: disable=W0703
            return self.NotFound(message="QS: Error with parameters.", exception=err)
        # get the format: returns a valid MIME-Type string to use in DataOutput
        if not params:
            params = {}
        try:
            queryformat = self.format(request, params, _format)
        except ValueError:
            queryformat = 'json'
        # get conditions
        conditions = {**options, **params}
        self.logger.debug(f'Test Slug: {slug}, format: {queryformat}, conditions: {conditions}')
        try:
            query = await self.get_source(request, slug, conditions, driver=args)
            result, error = await query.dry_run()
            if error:
                works = False
            # making dry-run to test if query works:
            try:
                explain, error = await query.provider.connection().query(f"EXPLAIN ANALYZE {result}")
                if error:
                    works = False
                elif explain is not None:
                    execution = explain.pop()['QUERY PLAN']
            except Exception as err:  # pylint: disable=W0718
                error = f"Failed Query: {err}"
                works = False
            ended = datetime.now()
            generated_at = (ended - started).total_seconds()
            if queryformat == 'json':
                result = result.replace('\r\n', ' ')
                resultset = {
                    "slug": slug,
                    "works": works,
                    "error": error,
                    "generated": generated_at,
                    "execution": execution
                }
                if not ignore_query:
                    resultset['conditions'] = conditions
                    resultset['query'] = result
                return self.json_response(resultset, status=200)
            elif queryformat in ('txt', 'plain', 'raw'):
                return self.response(
                    response=result,
                    content_type='text/plain'
                )

        except SlugNotFound as err:
            return self.NotFound(message=f"{err!s}", exception=err)
        except ParserError as err:
            return self.Error(
                message=f"Error parsing Query Slug {slug}",
                exception=err
            )
        except (ProviderError, DriverError) as err:
            return self.Error(
                message="Connection Error",
                exception=err
            )
        except Exception as ex:
            raise self.Except(
                message="Unknown Error on Query",
                exception=ex
            ) from ex
        finally:
            await query.close()

    async def clean_cache(self, request):
        """
        ---
        description: Clean the redis Cache associated to a Named Query
        summary: Clean the Redis Cache
        tags:
        - QuerySource
        produces:
        - application/json
        parameters:
        - name: slug
          description: slug Id of Query
          in: path
          required: true
          type: string
        responses:
            "202":
                description: Accepted, the Cache will be erased
            "204":
                description: No Data was found
            "403":
                description: Forbidden Execution
            "404":
                description: Query with name doesn't exists
            "405":
                description: invalid HTTP Method
            "406":
                description: Query Error
        """
        slug = request.match_info.get('slug')
        response_obj = {
            'x-status': 'empty',
            'x-message': f'Service Not Implemented, yet!: {slug}'
        }
        raise self.Except(
            message=f'Service Not Implemented, yet!: {slug}',
            headers=response_obj,
            code=501
        )
