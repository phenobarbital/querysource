# Import Config Class
import sys
import os
from pathlib import Path
from navconfig import BASE_DIR, config
from navconfig.logging import logging


### Matplotlib Configuration
logging.getLogger(name='matplotlib').setLevel(logging.WARNING)
logging.getLogger(name='matplotlib.font_manager').setLevel(logging.ERROR)
logging.getLogger(name='PIL').setLevel(logging.ERROR)
logging.getLogger(name='PIL.PngImagePlugin').setLevel(logging.ERROR)
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
async_default_dsn = f'postgresql+asyncpg://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'
sqlalchemy_url = f'postgresql://{DBUSER}:{DBPWD}@{DBHOST}:{DBPORT}/{DBNAME}'

# POSTGRESQL used by QuerySource:
PG_DRIVER = config.get('PG_DRIVER', fallback='pg')
PG_HOST = config.get('PG_HOST', fallback='localhost')
PG_USER = config.get('PG_USER')
PG_PWD = config.get('PG_PWD')
PG_DATABASE = config.get('PG_DATABASE', fallback='navigator')
PG_PORT = config.get('PG_PORT', fallback=5432)

asyncpg_url = f'postgres://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'
database_url = f'postgresql://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'
async_database_url = f'postgresql+asyncpg://{PG_USER}:{PG_PWD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}'
SQLALCHEMY_DATABASE_URI = database_url

POSTGRES_TIMEOUT = config.get('POSTGRES_TIMEOUT', fallback=3600000)
POSTGRES_MIN_CONNECTIONS = config.getint('POSTGRES_MIN_CONNECTIONS', fallback=2)
POSTGRES_MAX_CONNECTIONS = config.getint('POSTGRES_MAX_CONNECTIONS', fallback=200)

DB_TIMEOUT = config.getint("DB_TIMEOUT", fallback=3600)
DB_STATEMENT_TIMEOUT = config.get("DB_STATEMENT_TIMEOUT", fallback="3600000")
DB_SESSION_TIMEOUT = config.get('DB_SESSION_TIMEOUT', fallback="60min")
DB_IDLE_IN_TRANSACTION_TIMEOUT = config.get(
    'DB_IDLE_IN_TRANSACTION_TIMEOUT',
    fallback="60min"
)
DB_KEEPALIVE_IDLE = config.get('DB_KEEPALIVE_IDLE', fallback="30min")
DB_MAX_WORKERS = config.get('DB_MAX_WORKERS', fallback=128)

POSTGRES_SSL = config.getboolean('POSTGRES_SSL', fallback=False)
POSTGRES_SSL_CA = config.get('POSTGRES_SSL_CA')
POSTGRES_SSL_CERT = config.get('POSTGRES_SSL_CERT')
POSTGRES_SSL_KEY = config.get('POSTGRES_SSL_KEY')

# Timezone (For parsedate)
TIMEZONE = config.get("timezone", section="l18n", fallback="UTC")
USE_TIMEZONE = config.getboolean("USE_TIMEZONE", fallback=True)

DEFAULT_TIMEZONE = config.get(
    "default_timezone", section="l18n", fallback="America/New_York"
)
SYSTEM_LOCALE = config.get("locale", section="l18n", fallback="en_US.UTF-8")

### QuerySet (for QuerySource)
CACHE_HOST = config.get('CACHE_HOST', fallback='localhost')
CACHE_PORT = config.get('CACHE_PORT', fallback=6379)
CACHE_DB = config.get('CACHE_DB', fallback=0)
CACHE_URL = f"redis://{CACHE_HOST}:{CACHE_PORT!s}/{CACHE_DB}"
DEFAULT_SLUG_CACHE_TTL = config.getint(
    'DEFAULT_SLUG_CACHE_TTL',
    fallback=3600
)

## Redis as Database:
REDIS_HOST = config.get('REDIS_HOST', fallback='localhost')
REDIS_PORT = config.get('REDIS_PORT', fallback=6379)
REDIS_DB = config.get('REDIS_DB', fallback=1)
REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT!s}/{REDIS_DB}"

# QuerySet Cache (cache for queries)
QUERYSET_DB = config.get('QUERYSET_DB', fallback=3)
QUERYSET_REDIS = f"redis://{REDIS_HOST}:{REDIS_PORT}/{QUERYSET_DB}"
DEFAULT_QUERY_TIMEOUT = config.getint('DEFAULT_QUERY_TIMEOUT', fallback=3600)

### Memcache
MEMCACHE_DRIVER = config.get('MEMCACHE_DRIVER', fallback='memcache')
MEMCACHE_HOST = config.get('MEMCACHE_HOST', fallback='localhost')
MEMCACHE_PORT = config.getint('MEMCACHE_PORT', fallback=11211)
MEMCACHE_SERVICE = {
    'host': MEMCACHE_HOST,
    'port': MEMCACHE_PORT,
}

### Redash System
REDASH_HOST = config.get('REDASH_HOST')
REDASH_API_KEY = config.get('REDASH_API_KEY')


## Profiling:
URL_PROFILING = config.get('URL_PROFILING', fallback='http://localhost:5000')
### Resource Usage
API_TIMEOUT = 36000  # 10 minutes
SEMAPHORE_LIMIT = int(
    config.getint('SEMAPHORE_LIMIT', fallback=163840)
)

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
MSSQL_PWD = config.get('MSSQL_PWD')
MSSQL_DATABASE = config.get('MSSQL_DATABASE')

### Microsoft SQL Server
SQLSERVER_DRIVER = config.get('SQLSERVER_DRIVER', fallback='sqlserver')
SQLSERVER_HOST = config.get('SQLSERVER_HOST', fallback='127.0.0.1')
SQLSERVER_PORT = config.get('SQLSERVER_PORT', fallback=1433)
SQLSERVER_USER = config.get('SQLSERVER_USER')
SQLSERVER_PWD = config.get('SQLSERVER_PWD')
SQLSERVER_DATABASE = config.get('SQLSERVER_DATABASE')
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

## SCYLLADB
SCYLLA_DRIVER = config.get('SCYLLA_DRIVER', fallback='scylladb')
SCYLLA_HOST = config.get('SCYLLA_HOST', fallback='127.0.0.1')
SCYLLA_PORT = config.get('SCYLLA_PORT', fallback='9042')
SCYLLA_USER = config.get('SCYLLA_USER')
SCYLLA_PWD = config.get('SCYLLA_PWD')
SCYLLA_DATABASE = config.get('SCYLLA_DATABASE')

## INFLUXDB
INFLUX_DRIVER = config.get('INFLUX_DRIVER', fallback='influx')
INFLUX_HOST = config.get('INFLUX_HOST', fallback='127.0.0.1')
INFLUX_PORT = config.get('INFLUX_PORT', fallback='8086')
INFLUX_USER = config.get('INFLUX_USER')
INFLUX_PWD = config.get('INFLUX_PWD')
INFLUX_DATABASE = config.get('INFLUX_DATABASE')
INFLUX_ORG = config.get('INFLUX_ORG', fallback='navigator')
INFLUX_TOKEN = config.get('INFLUX_TOKEN')
INFLUX_LOGGING = config.get('INFLUX_LOGGING', fallback='navigator_logs')

# BigQuery Service:
bq_file = config.get('BIGQUERY_CREDENTIALS', fallback='env/google/bigquery.json')
bq_file = Path(bq_file).resolve()
if not bq_file.exists():
    bq_file = BASE_DIR.joinpath('env', 'google', 'bigquery.json')

## Bigquery Credentials
BIGQUERY_CREDENTIALS = config.get('BIGQUERY_CREDENTIALS', fallback=bq_file)
if isinstance(BIGQUERY_CREDENTIALS, str):
    BIGQUERY_CREDENTIALS = Path(BIGQUERY_CREDENTIALS).resolve()
BIGQUERY_PROJECT_ID = config.get('BIGQUERY_PROJECT_ID')

# this is the backend for saving Query Execution
ENVIRONMENT = config.get('ENVIRONMENT', fallback='development')
USE_INFLUX = config.getboolean('USE_INFLUX', fallback=True)
QS_EVENT_BACKEND = config.get('QS_EVENT_BACKEND', fallback='influx')
QS_EVENT_TABLE = config.get('QS_EVENT_TABLE', fallback='querysource')
QS_EVENT_CREDENTIALS = {
    "host": INFLUX_HOST,
    "port": INFLUX_PORT,
    "bucket": INFLUX_DATABASE,
    "org": INFLUX_ORG,
    "token": INFLUX_TOKEN
}

# RETHINKDB
RT_DRIVER = config.get('RT_DRIVER', fallback='rethink')
RT_HOST = config.get('RT_HOST', fallback='localhost')
RT_PORT = config.get('RT_PORT', fallback=28015)
RT_DATABASE = config.get('RT_DATABASE', fallback='navigator')
RT_USER = config.get('RT_USER', fallback=None)
RT_PASSWORD = config.get('RT_PWD', fallback=None)

# MongoDB
MONGO_DRIVER = config.get('MONGO_DRIVER', fallback='mongo')
MONGO_HOST = config.get('MONGO_HOST', fallback='localhost')
MONGO_PORT = config.get('MONGO_PORT', fallback=27017)
MONGO_DATABASE = config.get('MONGO_DATABASE', fallback='navigator')
MONGO_USER = config.get('MONGO_USER')
MONGO_PASSWORD = config.get('MONGO_PWD')

# DocumentDB configuration:
DOCUMENTDB_HOSTNAME = config.get('DOCUMENTDB_HOSTNAME', fallback='localhost')
DOCUMENTDB_PORT = config.get('DOCUMENTDB_PORT', fallback=27017)
DOCUMENTDB_DATABASE = config.get('DOCUMENTDB_DATABASE', fallback='navigator')
DOCUMENTDB_USERNAME = config.get('DOCUMENTDB_USERNAME')
DOCUMENTDB_PASSWORD = config.get('DOCUMENTDB_PASSWORD')
DOCUMENTDB_TLSFILE = config.get('DOCUMENTDB_TLSFILE')
DOCUMENTDB_USE_SSL = config.getboolean('DOCUMENTDB_USE_SSL', fallback=True)
if isinstance(DOCUMENTDB_TLSFILE, str):
    DOCUMENTDB_TLSFILE = Path(DOCUMENTDB_TLSFILE).resolve()
    if not DOCUMENTDB_TLSFILE.exists():
        DOCUMENTDB_TLSFILE = None
if not DOCUMENTDB_TLSFILE:
    DOCUMENTDB_TLSFILE = BASE_DIR.joinpath('env', 'global-bundle.pem')

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
JIRA_TOKEN = config.get('JIRA_TOKEN')
JIRA_CERT = config.get('JIRA_CERT')

# Google Analytics
GOOGLE_SERVICE_FILE = config.get('GA_SERVICE_ACCOUNT_NAME', fallback="ga-api-a78f7d886a47.json")
GOOGLE_SERVICE_PATH = config.get('GA_SERVICE_PATH', fallback=BASE_DIR.joinpath("env"))
GA_SERVICE_ACCOUNT_NAME = "google.json"
GA_SERVICE_PATH = "google/"

### SalesForce:
SALESFORCE_COMPANY = config.get('SALESFORCE_COMPANY')
SALESFORCE_INSTANCE = config.get('SALESFORCE_INSTANCE')
SALESFORCE_TOKEN = config.get('SALESFORCE_TOKEN')
SALESFORCE_DOMAIN = config.get('SALESFORCE_DOMAIN', fallback="test")
SALESFORCE_USERNAME = config.get('SALESFORCE_USERNAME')
SALESFORCE_PASSWORD = config.get('SALESFORCE_PASSWORD')

## ClickHouse:
CLICKHOUSE_DRIVER = config.get('CLICKHOUSE_DRIVER', fallback='clickhouse')
CLICKHOUSE_HOST = config.get('CLICKHOUSE_HOST', fallback='localhost')
CLICKHOUSE_PORT = config.getint('CLICKHOUSE_PORT', fallback=9000)
CLICKHOUSE_USER = config.get('CLICKHOUSE_USER')
CLICKHOUSE_PASSWORD = config.get('CLICKHOUSE_PASSWORD')
CLICKHOUSE_DATABASE = config.get('CLICKHOUSE_DATABASE', fallback='default')
CLICKHOUSE_SECURE = config.getboolean('CLICKHOUSE_SECURE', fallback=False)
CLICKHOUSE_CLIENT_NAME = config.get('CLICKHOUSE_CLIENT_NAME', fallback='Navigator')


## Export Options (Output):
CSV_DEFAULT_DELIMITER = config.get('CSV_DEFAULT_DELIMITER', fallback=',')
CSV_DEFAULT_QUOTING = config.get('CSV_DEFAULT_QUOTING', fallback='string')

## QuerySource Model:
QS_QUERIES_SCHEMA = config.get('QS_QUERIES_SCHEMA', fallback='public')
QS_QUERIES_TABLE = config.get('QS_QUERIES_TABLE', fallback='queries')

## QuerySource Query Timeout:
DEFAULT_QUERY_TIMEOUT = config.getint('DEFAULT_QUERY_TIMEOUT', fallback=3600)
DEFAULT_QUERY_FORMAT = config.get(
    'DEFAULT_QUERY_FORMAT',
    fallback='native'
)

## Geoloc Support:
GEOLOC_API_KEY = config.get('GEOLOC_API_KEY')

### Query Cache functionality:
"""
RabbitMQ Configuration.
"""
USE_RABBITMQ = config.getboolean('USE_RABBITMQ', fallback=False)
RABBITMQ_HOST = config.get("RABBITMQ_HOST", fallback="localhost")
RABBITMQ_PORT = config.get("RABBITMQ_PORT", fallback=5672)
RABBITMQ_USER = config.get("RABBITMQ_USER", fallback="guest")
RABBITMQ_PASS = config.get("RABBITMQ_PASS", fallback="guest")
RABBITMQ_VHOST = config.get("RABBITMQ_VHOST", fallback="navigator")
# DSN
rabbitmq_dsn = f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASS}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/{RABBITMQ_VHOST}"
BROKER_MANAGER_QUEUE_SIZE = config.getint(
    "BROKER_MANAGER_QUEUE_SIZE",
    fallback=4
)

# Dask Cluster:
USE_DASK = config.getboolean("USE_DASK", fallback=False)
DASK_SCHEDULER = config.get("DASK_SCHEDULER", fallback="tcp://127.0.0.1:8786")
DASK_SCHEDULER_PORT = config.get(
    "DASK_SCHEDULER_PORT",
    fallback=8786
)

# Modin Cluster:
USE_MODIN = config.getboolean("USE_MODIN", fallback=False)
MODIN_CLUSTER_CONFIG = config.get(
    "MODIN_CLUSTER_CONFIG",
    fallback="local"
)
MODIN_SERVER = config.get("MODIN_SERVER", fallback="tcp://127.0.0.1:8786")

# Vector Models:
USE_VECTORS = config.getboolean("USE_VECTORS", fallback=True)
vector_models = config.getlist("VECTOR_MODELS")
if not vector_models:
    vector_models = ["word2vec-google-news-300"]

# Gensim Folder:
GENSIM_DATA_DIR = config.get(
    'GENSIM_DATA_DIR',
    fallback=BASE_DIR.joinpath('data', 'gensim')
)
os.environ['GENSIM_DATA_DIR'] = str(GENSIM_DATA_DIR)

try:
    from settings.settings import *  # pylint: disable=W0614,W0401 # noqa
except ImportError:
    pass
