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
from querysource.datasources.models import DataSource
from querysource.datasources.drivers import SUPPORTED
from querysource.conf import default_dsn
from querysource.utils.parseqs import ParseDict
from querysource.exceptions import ParserError


class DatasourceView(BaseView):
    """API View for managing datasources.
    """

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
                driver =  {
                    "uid": uuid.uuid1(),
                    "driver": drv.driver,
                    "name": name,
                    "description": drv.name,
                    "params": drv.get_parameters(),
                    "credentials": drv.get_credentials(),
                    "program_slug": "default",
                    "drv": drv.modelName,
                    "default": True
                }
                if drv.dsn is not None:
                    driver['dsn'] = drv.dsn
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
        except (KeyError, ValueError): # pylint: disable=W0703
            ds = None
        # getting all datasources based on ds variable:
        db = self.get_connection()
        fields = ["uid", "driver", "name", "description", "params", "credentials", "dsn", "program_slug"]
        if not ds:
            try:
                async with await db.connection() as conn:
                    DataSource.Meta.connection = conn
                    if not filtering:
                        fn = partial(DataSource.all, fields=fields)
                    else:
                        fn = partial(DataSource.filter, **filtering, fields=fields)
                    try:
                        result = await fn()
                        headers = {
                            'X-STATUS': 'OK',
                            'X-MESSAGE': 'Datasource Information'
                        }
                        default = self.default_sources()
                        if not filtering:
                            result = result + default
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
                DataSource.Meta.connection = conn
                try:
                    result = await DataSource.get(name=ds)
                    try:
                        # TODO: removing "secrets"
                        result.credentials['password'] = '(hidden)'
                    except KeyError:
                        pass
                    headers = {
                        'X-STATUS': 'OK',
                        'X-MESSAGE': f'Datasource Information: {ds}'
                    }
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
            args = {k:v for k,v in data.items() if k not in removed}
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
        data = await self.json_data()
        ## first, getting the driver:
        try:
            driver, drvname = self.get_driver(data)
        except ValueError as ex:
            return self.error(
                response= {
                  "error": f"{ex!s}"
                },
                status = 400
            )
        except ValidationError as ex:
            return self.error(
                response= {
                  "error": f"There are errors on Driver information: {ex!s}",
                  "payload": str(ex.payload),
                },
                status = 400
            )
        except RuntimeError as ex:
            return self.error(
                response= {
                  "error": f"{ex!s}"
                },
                status = 400
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
                response = {
                    "message": f'Invalid dataSource using {attributes!s}',
                    "payload": str(ex.payload),
                },
                exception=ex,
                status=406
            )
        except Exception as err:
            return self.error(
                response = {
                    "error": f'Datasouce exception using: {attributes!s}'
                },
                exception=err,
                status=400
            )
        try:
            db = self.get_connection()
            async with await db.connection() as conn:
                datasource.Meta.connection = conn
                result = await datasource.insert()
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
                return self.json_response(
                    response=datasource,
                    headers=headers,
                    status=201
                )
        except Exception as err:
            if 'duplicate key' in str(err):
                return self.error(
                    response = {
                        "error": f'Duplicate Datasource: {err!s}',
                    },
                    exception=err,
                    status=409
                )
            else:
                return self.error(
                    response = {
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
                DataSource.Meta.connection = conn
                try:
                    datasource = await DataSource.get(**ds)
                    headers = {
                        'X-STATUS': 'OK',
                        'X-MESSAGE': f'{datasource.name} Information'
                    }
                    await datasource.delete()
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
        data = await self.json_data()
            ## first, getting the driver:
        try:
            driver, drvname = self.get_driver(data)
        except ValueError as ex:
            return self.error(
                response= {
                  "error": f"{ex!s}"
                },
                status = 400
            )
        except ValidationError as ex:
            return self.error(
                response= {
                  "error": f"There are errors on Driver information: {ex!s}",
                  "payload": str(ex.payload),
                },
                status = 400
            )
        except RuntimeError as ex:
            return self.error(
                response= {
                  "error": f"{ex!s}"
                },
                status = 400
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
                DataSource.Meta.connection = conn
                if ds is not None:
                    try:
                        dt = await DataSource.get(**ds)
                        attributes = {**dt.dict(), **data}
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
                        print(datasource)
                    except ValueError  as ex:
                        return self.error(
                            response = {
                                "message": f'Invalid dataSource using {attributes!s}',
                                "error": str(ex),
                            },
                            status=406
                        )
                    except ValidationError as ex:
                        return self.error(
                            response = {
                                "message": f'Invalid dataSource using {attributes!s}',
                                "payload": str(ex.payload),
                            },
                            exception=ex,
                            status=406
                        )
                ### Saving Datasource:
                try:
                    if found:
                        result = await datasource.update()
                        status = 202
                    else:
                        result = await datasource.insert()
                        status = 201
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

    # async def patch(self):
    #     """
    #     PATCH Method.
    #     description: updating partially info about a Datasource
    #     tags:
    #     - Datasource
    #     - datasources
    #     - Database connections
    #     consumes:
    #     - application/merge-patch+json
    #     produces:
    #     - application/json
    #     responses:
    #         "200":
    #             description: Existing Datasource was updated.
    #         "201":
    #             description: New Datasource was inserted
    #         "304":
    #             description: Datasource not modified, its currently the actual version
    #         "403":
    #             description: Forbidden Call
    #         "404":
    #             description: No Data was found
    #         "406":
    #             description: Query Error
    #         "409":
    #             description: Conflict, a constraint was violated
    #     """
    #     headers = {
    #         'X-STATUS': 'EMPTY',
    #         'X-MESSAGE': 'Data not Found'
    #     }
    #     return self.no_content(headers=headers)
