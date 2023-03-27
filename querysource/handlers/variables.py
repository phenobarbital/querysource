import logging
from datetime import datetime
from asyncdb import AsyncDB
from navigator.views import BaseView
from querysource.conf import REDIS_URL, default_dsn
from querysource.utils.functions import format_date


class VariablesService(BaseView):
    def get_redis(self):
        ## redis connection:
        return AsyncDB(
            'redis',
            dsn=REDIS_URL
        )

    def get_db(self):
        return AsyncDB(
            'pg',
            dsn=default_dsn
        )

    async def get_variables(self, program: str = None, module: str = None, variable:str=None):
        sql = "SELECT pid, variable_name, raw_value, variable_name, program_id, attributes FROM troc.process_variables"
        result = None
        try:
            if program:
                sql = f"{sql} WHERE program_slug = '{program}'"
            if module:
                sql = f"{sql} AND module_id = '{module}'"
            if variable:
                sql = f"{sql} AND variable_name = '{variable.upper()}'"
            db = self.get_db()
            async with await db.connection() as conn:
                result, _ = await conn.query(sql)
            return result
        except Exception:
            return None

    async def get_program_id(self, program):
        sql = f"SELECT program_id FROM troc.troc_programs WHERE program_slug = '{program!s}'"
        try:
            db = self.get_db()
            async with await db.connection() as conn:
                result, _ = await conn.queryrow(sql)
                return result["program_id"]
        except Exception:
            return None

    async def get(self):
        """
        ---
        description: Get all the operational variables in the current scope and program
        summary: get the operational variables and values
        tags:
        - VariablesService
        produces:
        - application/json
        parameters:
        - name: program
          description: program id to filter
          in: path
          required: false
          type: integer
        - name: variable
          description: variable name
          in: path
          required: false
          type: string
        responses:
            "200":
                description: returns valid data
            "204":
                description: No data
            "403":
                description: Forbidden Call
            "404":
                description: Program o Variable not found
            "406":
                description: Query Error
        """
        try:
            program = None
            module = None
            variable = None
            params = self.get_args()
            attributes = self.query_parameters(request=self.request)
            try:
                redis = bool(attributes["redis"])
                _redis = self.get_redis()
                print('R ', redis, _redis)
            except KeyError:
                redis = False
                _redis = None
            # try:
            #     session = self.request["session"]
            # except KeyError:
            #     session = None
            if params:
                # we need an especific program
                try:
                    program = params["program"]
                except KeyError:
                    program = None
                    module = None
                try:
                    variable = params["variable"]
                except KeyError:
                    pass
                try:
                    module = params["module"]
                except KeyError:
                    pass
            result = await self.get_variables(program, module, variable)
            if result:
                data = {}
                for row in result:
                    key = row["variable_name"]
                    if redis is True:
                        # getting value from REDIS instead DB:
                        async with await _redis.connection() as rd:
                            value = await rd.get(key)
                    else:
                        value = row["raw_value"]
                    data[key] = value
                headers = {"x-status": "OK", "x-message": "Data OK"}
                return self.json_response(response=data, headers=headers)
            else:
                headers = {"x-status": "Empty", "x-message": "Data not Found"}
                return self.no_content(headers=headers)
        except Exception as e:
            return self.critical(e)

    async def update_variables(self, result):
        data = {}
        for row in result:
            variable = row["variable_name"]
            attributes = row["attributes"]
            program = row["program_id"]
            _redis = self.get_redis()
            db = self.get_db()
            if attributes:
                if "table" in attributes:
                    # first logic: using table and column
                    f = attributes["column"]
                    t = attributes["table"]
                    try:
                        value = None
                        sql = 'SELECT "{field}" FROM {table} WHERE "{field}" is not null ORDER BY "{field}" DESC LIMIT 1'.format(
                            field=f, table=t
                        )
                        logging.debug(sql)
                        async with await db.connection() as conn:
                            row, _ = await conn.queryrow(sql)
                            try:
                                format = attributes["format"]
                            except KeyError:
                                format = "%Y-%m-%d"
                            value = format_date(row[f], format)
                            sql = "UPDATE troc.process_variables SET raw_value = '{value}' WHERE program_id = {program} AND variable_name = '{variable}'".format(
                                value=value, program=program, variable=variable
                            )
                            ok, _ = await conn.execute(sql)
                            if ok:
                                async with await _redis.connection() as rd:
                                    await rd.set(variable, value)
                            data[variable] = value
                    except Exception as err:
                        print(err)
                        continue
                elif "sql" in attributes:
                    try:
                        # second logic: run a raw sql to got the first value of data
                        sql = attributes["sql"]
                        try:
                            format = attributes["format"]
                        except KeyError:
                            format = "%Y-%m-%d"
                        async with await db.connection() as conn:
                            row, _ = await conn.queryrow(sql)
                            value = format_date(row["value"], format)
                            up = f"UPDATE troc.process_variables SET raw_value = '{value}' WHERE program_id = {program} AND variable_name = '{variable}'"
                            ok = await conn.execute(up)
                            if ok:
                                async with await self._redis.connection() as redis:
                                    # set on REDIS too
                                    await redis.set(variable, value)
                                data[variable] = value
                    except Exception as err:
                        print(err)
                        continue
                elif "fn" in attributes:
                    # third logic: using a python function to updating the variable
                    fn = attributes["fn"]
                    continue  # TODO
                else:
                    continue  # invalid functionality
            else:
                continue
        return data

    async def put(self):
        """
        Put.
           update a variable using algorithm
        """
        try:
            # TODO: add validations to value
            program = None
            module = None
            variable = None
            params = self.get_arguments()
            if params:
                # we need an especific program
                try:
                    program = params["program"]
                except KeyError:
                    program = None
                    module = None
                try:
                    variable = params["variable"]
                except KeyError:
                    pass
                try:
                    module = params["module"]
                except KeyError:
                    pass
            result = await self.get_variables(program, module, variable)
            if result:
                data = await self.update_variables(result)
                if data:
                    headers = {"x-status": "OK", "x-message": "Data OK"}
                    return self.json_response(response=data, headers=headers)
                else:
                    headers = {"x-status": "Empty", "x-message": "Data not Found"}
                    return self.no_content(headers=headers)
            else:
                # explicit creation, avoid accidental creation
                if "create" in params:
                    # need to create this new variable:
                    content = await self.json_data()
                    try:
                        data = {
                            "process_name": variable,
                            "raw_value": content["value"],
                            "program_id": await self.get_program_id(program),
                            "variable_name": variable.upper(),
                            "program_slug": program,
                            "updated_at": datetime.now(),
                        }
                        _redis = self.get_redis()
                        db = self.get_db()
                        insert = "INSERT INTO troc.process_variables(process_name, raw_value, program_id, variable_name, program_slug, updated_at) VALUES ($1, $2, $3, $4, $5, $6)"
                        async with await db.connection() as conn:
                            smt, _ = await conn.prepare(insert)
                            result = await smt.fetchrow(*data.values())
                            async with await self._redis.connection() as redis:
                                    # set on REDIS too
                                    await redis.set(variable, content["value"])
                        headers = {
                            "x-status": "OK",
                            "x-message": f"New Variable Inserted {variable}",
                            "x-value": content["value"],
                        }
                        return self.json_response(response=data, headers=headers)
                    except Exception as e:
                        print(e)
                        return self.critical(self.request, e)
                else:
                    headers = {"x-status": "Empty", "x-message": "Data not Found"}
                    return self.no_content(headers=headers)
        except Exception as e:
            print(e)
            return self.critical(e)

    async def post(self):
        """
        post.
           update a variable (or variables) using a json object
        """
        try:
            # TODO: add validations to value
            program = None
            module = None
            variable = None
            params = self.get_arguments()
            content = await self.json_data()
            _redis = self.get_redis()
            db = self.get_db()
            if params:
                # we need an especific program
                try:
                    program = params["program"]
                except KeyError:
                    program = None
                    module = None
                try:
                    variable = params["variable"]
                except KeyError:
                    pass
                try:
                    module = params["module"]
                except KeyError:
                    pass
            # if content exists, I need to used it
            if content:
                data = {}
                if variable:
                    # need to update only one variable:
                    value = content["value"]
                    sql = f"UPDATE troc.process_variables SET raw_value = '{value}' WHERE program_slug = '{program}' AND variable_name = '{variable}'"
                    try:
                        async with await db.connection() as conn:
                            result, _ = await conn.execute(sql)
                            if result:
                                async with await self._redis.acquire() as redis:
                                    # set on REDIS too
                                    await redis.set(variable, value)
                                data[variable] = value
                                headers = {"x-status": "OK", "x-message": "Updated OK"}
                                return self.json_response(response=data, headers=headers)
                    except Exception as err:
                        self.error(
                            exception=err,
                            response=f"Error Updating Variable {program}{variable}",
                            state=500,
                        )
                else:
                    for key, value in content.items():
                        var = key.upper()
                        sql = "UPDATE troc.process_variables SET raw_value = '{value}' WHERE program_slug = '{program}' AND variable_name = '{variable}'".format(
                            value=value, program=program, variable=key
                        )
                        async with await db.connection() as conn:
                            result, _ = await conn.execute(sql)
                            if result:
                                async with await _redis.connection() as redis:
                                    # set on REDIS too
                                    await redis.set(key, value)
                            data[var] = value
                    if data:
                        headers = {"x-status": "OK", "x-message": "Updated OK"}
                        return self.json_response(response=data, headers=headers)
            else:
                result = await self.get_variables(program, module, variable)
                if result:
                    data = await self.update_variables(result)
                    headers = {"x-status": "OK", "x-message": "Updated OK"}
                    return self.json_response(response=data, headers=headers)
                else:
                    # TODO: we need to set using rules
                    return self.error(response="No value in content", state=400)
        except Exception as e:
            print(e)
            return self.critical(self.request, e)
        finally:
            await self.close()
