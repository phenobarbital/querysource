# Import Config Class
import sys
import os
from navconfig import BASE_DIR, config


### Matplotlib Configuration
mpldir = config.get('MPLCONFIGDIR', fallback=BASE_DIR.joinpath('templates'))
os.environ['MPLCONFIGDIR'] = str(mpldir)

### Plugins Folder:
PLUGINS_FOLDER = BASE_DIR.joinpath('plugins')
### also, Add plugins folder to sys.path
sys.path.append(str(PLUGINS_FOLDER))
### Databases

# DB Default (database used for interaction (rw))
DBHOST = config.get('DBHOST', fallback='localhost')
DBUSER = config.get('DBUSER')
DBPWD = config.get('DBPWD')
DBNAME = config.get('DBNAME', fallback='navigator')
DBPORT = config.get('DBPORT', fallback=5432)
if not DBUSER:
    raise RuntimeError('Missing PostgreSQL Default Settings.')
# database for changes (admin)
default_dsn = f'postgres://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'


# POSTGRESQL used by QuerySource:
PG_DRIVER = config.get('PG_DRIVER', fallback='pg')
PG_HOST = config.get('PG_HOST', fallback='localhost')
PG_USER = config.get('PG_USER')
PG_PWD = config.get('PG_PWD')
PG_DATABASE = config.get('PG_DATABASE', fallback='navigator')
PG_PORT = config.get('PG_PORT', fallback=5432)
if not PG_USER:
    raise RuntimeError('Missing PostgreSQL Settings.')


POSTGRES_TIMEOUT = config.get('POSTGRES_TIMEOUT', fallback=3600000)
POSTGRES_MIN_CONNECTIONS = config.getint('POSTGRES_MIN_CONNECTIONS', fallback=2)
POSTGRES_MAX_CONNECTIONS = config.getint('POSTGRES_MAX_CONNECTIONS', fallback=200)
POSTGRES_SSL = config.getboolean('POSTGRES_SSL', fallback=False)
POSTGRES_SSL_CA = config.get('POSTGRES_SSL_CA')
POSTGRES_SSL_CERT = config.get('POSTGRES_SSL_CERT')
POSTGRES_SSL_KEY = config.get('POSTGRES_SSL_KEY')

database_url = f'postgresql://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'
SQLALCHEMY_DATABASE_URI = database_url
asyncpg_url = f'postgres://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'

### QuerySet (for QuerySource)
CACHE_HOST = config.get('CACHEHOST', fallback='localhost')
CACHE_PORT = config.get('CACHEPORT', fallback=6379)
CACHE_DB = config.get('CACHEDB', fallback=2)
CACHE_URL = f"redis://{CACHE_HOST}:{CACHE_PORT!s}/{CACHE_DB}"

## Redis as Database:
REDIS_HOST = config.get('REDIS_HOST', fallback='localhost')
REDIS_PORT = config.get('REDIS_PORT', fallback=6379)
REDIS_DB = config.get('REDIS_DB', fallback=2)
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT!s}/{REDIS_DB}"

# QuerySet Cache (cache for queries)
QUERYSET_DB = config.get('QUERYSET_DB', fallback=3)
QUERYSET_REDIS = f"redis://{REDIS_HOST}:{REDIS_PORT}/{QUERYSET_DB}"


### Memcache
MEMCACHE_HOST = config.get('MEMCACHE_HOST', 'localhost')
MEMCACHE_PORT = config.get('MEMCACHE_PORT', 11211)

### Redash System
REDASH_HOST = config.get('REDASH_HOST')
REDASH_API_KEY = config.get('REDASH_API_KEY')


## Profiling:
URL_PROFILING = config.get('URL_PROFILING', fallback='http://localhost:5000')
### Resource Usage
API_TIMEOUT = 36000  # 10 minutes
SEMAPHORE_LIMIT = config.get('SEMAPHORE_LIMIT', fallback=4096)

### Other database support:
## MYSQL
MYSQL_DRIVER = config.get('MYSQL_DRIVER', fallback='mysql')
MYSQL_HOST = config.get('MYSQL_HOST', fallback='127.0.0.1')
MYSQL_PORT = config.get('MYSQL_PORT', fallback='3306')
MYSQL_USER = config.get('MYSQL_USER')
MYSQL_PWD = config.get('MYSQL_PWD')
MYSQL_DATABASE = config.get('MYSQL_DATABASE')

### SQL Server (low-driver)
MSSQL_DRIVER = config.get('MSSQL_DRIVER', fallback='mssql')
MSSQL_HOST = config.get('MSSQL_HOST', fallback='127.0.0.1')
MSSQL_PORT = config.get('MSSQL_PORT', fallback='1407')
MSSQL_USER = config.get('MSSQL_USER')
MSSQL_PWD= config.get('MSSQL_PWD')
MSSQL_DATABASE = config.get('MSSQL_DATABASE')

### Microsoft SQL Server
SQLSERVER_DRIVER = config.get('SQLSERVER_DRIVER', fallback='sqlserver')
SQLSERVER_HOST = config.get('SQLSERVER_HOST', fallback='127.0.0.1')
SQLSERVER_PORT = config.get('SQLSERVER_PORT', fallback=1433)
SQLSERVER_USER  = config.get('SQLSERVER_USER')
SQLSERVER_PWD  = config.get('SQLSERVER_PWD')
SQLSERVER_DATABASE  = config.get('SQLSERVER_DATABASE')
SQLSERVER_TDS_VERSION = config.get('SQLSERVER_TDS_VERSION', fallback='8.0')

## ORACLE
ORACLE_DRIVER = config.get('ORACLE_DRIVER', fallback='oracle')
ORACLE_HOST = config.get('ORACLE_HOST', fallback='127.0.0.1')
ORACLE_PORT = config.get('ORACLE_PORT', fallback=1521)
ORACLE_USER = config.get('ORACLE_USER')
ORACLE_PWD = config.get('ORACLE_PWD')
ORACLE_DATABASE = config.get('ORACLE_DATABASE')
ORACLE_CLIENT = config.get('ORACLE_CLIENT')

## JDBC
JDBC_DRIVER = config.get('JDBC_DRIVER', fallback='oracle')
JDBC_HOST = config.get('JDBC_HOST', fallback='127.0.0.1')
JDBC_PORT = config.get('JDBC_PORT', fallback='1521')
JDBC_USER = config.get('JDBC_USER')
JDBC_PWD = config.get('JDBC_PWD')
JDBC_DATABASE = config.get('JDBC_DATABASE')
oracle_jar = BASE_DIR.joinpath('bin', 'jar', 'ojdbc8.jar')
JDBC_JAR = config.getlist('JDBC_JAR', fallback=oracle_jar)
JDBC_CLASSPATH = config.get('JDBC_CLASSPATH', fallback=BASE_DIR.joinpath('bin', 'jar'))

## CASSANDRA
CASSANDRA_DRIVER = config.get('CASSANDRA_DRIVER', fallback='cassandra')
CASSANDRA_HOST = config.get('CASSANDRA_HOST', fallback='127.0.0.1')
CASSANDRA_PORT = config.get('CASSANDRA_PORT', fallback='9042')
CASSANDRA_USER = config.get('CASSANDRA_USER')
CASSANDRA_PWD = config.get('CASSANDRA_PWD')
CASSANDRA_DATABASE = config.get('CASSANDRA_DATABASE')

## INFLUXDB
INFLUX_DRIVER = config.get('INFLUX_DRIVER', fallback='influx')
INFLUX_HOST = config.get('INFLUX_HOST', fallback='127.0.0.1')
INFLUX_PORT = config.get('INFLUX_PORT', fallback='8086')
INFLUX_USER = config.get('INFLUX_USER')
INFLUX_PWD = config.get('INFLUX_PWD')
INFLUX_DATABASE = config.get('INFLUX_DATABASE')
INFLUX_ORG = config.get('INFLUX_ORG', fallback='navigator')
INFLUX_TOKEN = config.get('INFLUX_TOKEN')


# RETHINKDB
rt_driver = config.get('RT_DRIVER', fallback='rethink')
rt_host = config.get('RT_HOST', fallback='localhost')
rt_port = config.get('RT_PORT', fallback=28015)
rt_database = config.get('RT_DATABASE', fallback='navigator')
rt_user = config.get('RT_USER')
rt_password = config.get('RT_PWD')

# MongoDB
mongo_driver = config.get('MONGO_DRIVER', fallback='mongo')
mongo_host = config.get('MONGO_HOST', fallback='localhost')
mongo_port = config.get('MONGO_PORT', fallback=27017)
mongo_database = config.get('MONGO_DATABASE', fallback='navigator')
mongo_user = config.get('MONGO_USER')
mongo_password = config.get('MONGO_PWD')


# Amazon AWS services:
DEFAULT_AWS_REGION = config.get('DEFAULT_AWS_REGION', fallback='us-east-1')

# DYNAMO DB:
DYNAMODB_REGION = config.get('DYNAMODB_REGION')
DYNAMODB_KEY = config.get('DYNAMODB_KEY')
DYNAMODB_SECRET = config.get('DYNAMODB_SECRET')

# Amazon Athena:
ATHENA_REGION = config.get('ATHENA_REGION')
ATHENA_KEY = config.get('ATHENA_KEY')
ATHENA_SECRET = config.get('ATHENA_SECRET')
ATHENA_BUCKET = config.get('ATHENA_BUCKET')
ATHENA_SCHEMA = config.get('ATHENA_SCHEMA')

## Jira JQL
JIRA_HOST = config.get('JIRA_HOST')
JIRA_USERNAME = config.get('JIRA_USERNAME')
JIRA_PASSWORD = config.get('JIRA_PASSWORD')

### Google Cloud
GOOGLE_SERVICE_FILE = config.get('GA_SERVICE_ACCOUNT_NAME', fallback="ga-api-a78f7d886a47.json")
GOOGLE_SERVICE_PATH = config.get('GA_SERVICE_PATH', fallback=BASE_DIR.joinpath("env"))

### SalesForce:
SALESFORCE_INSTANCE = config.get('SALESFORCE_INSTANCE')
SALESFORCE_TOKEN = config.get('SALESFORCE_TOKEN')
SALESFORCE_DOMAIN = config.get('SALESFORCE_DOMAIN', fallback="test")
SALESFORCE_USERNAME = config.get('SALESFORCE_USERNAME')
SALESFORCE_PASSWORD = config.get('SALESFORCE_PASSWORD')

## Export Options (Output):
CSV_DEFAULT_DELIMITER = config.get('CSV_DEFAULT_DELIMITER', fallback=',')
CSV_DEFAULT_QUOTING = config.get('CSV_DEFAULT_QUOTING', fallback='string')

## QuerySource Model:
QS_QUERIES_SCHEMA = config.get('QS_QUERIES_SCHEMA', fallback='public')
QS_QUERIES_TABLE = config.get('QS_QUERIES_TABLE', fallback='queries')
try:
    from settings.settings import *  # pylint: disable=W0614,W0401
except ImportError:
    pass
