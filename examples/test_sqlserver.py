"""
Test Cassandra resources.
"""
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
import asyncio
from datetime import datetime
import timeit

from asyncdb import AsyncDB
from asyncdb.exceptions import default_exception_handler
from querysource import QuerySource

from navconfig.conf import asyncpg_url, DEBUG, database_url

loop = asyncio.new_event_loop()
loop.set_exception_handler(default_exception_handler)

PARAMS = {
    "host": "localhost",
    "port": "1433",
    "database": 'AdventureWorks2019',
    "user": 'sa',
    "password": 'P4ssW0rd1.'
}

sql = """
SELECT TOP (1000) [BusinessEntityID]
      ,[PersonType]
      ,[NameStyle]
      ,[Title]
      ,[FirstName]
      ,[MiddleName]
      ,[LastName]
      ,[Suffix]
      ,[EmailPromotion]
      ,[AdditionalContactInfo]
      ,[Demographics]
      ,[rowguid]
      ,[ModifiedDate]
  FROM [AdventureWorks2019].[Person].[Person]
  """

connection = AsyncDB('sqlserver', params=PARAMS)

async def query():
    startTime = datetime.now()
    try:
        await connection.connection()
        print('IS MS SQL Server Connected: {}'.format(connection.is_connected()))
        connection.use('AdventureWorks2019')
        qry = QuerySource(
            query_raw=sql,
            conditions=None,
            connection=connection,
            loop=loop,
            driver={
              "driver": 'sqlserver',
              "datasource": PARAMS
            }
        )
        await qry.get_query()
        result, error = await qry.query()
        for row in result:
            print(row)
        await qry.close()
        print("Generated in: %s" % (datetime.now() - startTime))
    except Exception as err:
        print(err)
    finally:
        print("COMPLETED! ========")


if __name__ == '__main__':
    try:
        asyncio.run(query())
    finally:
         loop.stop()
