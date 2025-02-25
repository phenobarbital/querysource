"""
Source Tree about Data Drivers.
"""
from .abstract import BaseDriver
from .pg import pgDriver
from .redis import redisDriver
from .oracle import oracleDriver
from .jdbc import jdbcDriver
from .sqlserver import sqlserverDriver
from .influx import influxDriver
from .cassandra import cassandraDriver
from .rethink import rethinkDriver
from .mysql import mysqlDriver
from .mariadb import mariadbDriver
from .mongo import mongoDriver
from .sqlite import sqliteDriver
from .hazel import hazelDriver
from .salesforce import salesforceDriver
from .scylladb import scylladbDriver
from .bigquery import bigqueryDriver
from .documentdb import documentdbDriver


## List of Supported Drivers:
SUPPORTED = {
    'pg': {
        "name": "PostgreSQL (asyncpg version)",
        "driver": pgDriver
    },
    'redis': {
        "name": "Redis Server",
        "driver": redisDriver
    },
    'oracle': {
        "name": "Oracle Database",
        "driver": oracleDriver
    },
    'jdbc': {
        "name": "JDBC Connection",
        "driver": jdbcDriver
    },
    'sqlserver': {
        "name": "MS SQL Server",
        "driver": sqlserverDriver
    },
    "influx": {
        "name": "InfluxDB",
        "driver": influxDriver
    },
    "cassandra": {
        "name": "Apache Cassandra",
        "driver": cassandraDriver
    },
    "rethink": {
        "name": "RethinkDB",
        "driver": rethinkDriver
    },
    "mysql": {
        "name": "MySQL",
        "driver": mysqlDriver
    },
    "mariadb": {
        "name": "MariaDB",
        "driver": mariadbDriver
    },
    "mongo": {
        "name": "MongoDB",
        "driver": mongoDriver
    },
    "documentdb": {
        "name": "DocumentDB",
        "driver": documentdbDriver
    },
    "sqlite": {
        "name": "SQLite",
        "driver": sqliteDriver
    },
    "hazel": {
        "name": "Hazelcast",
        "driver": hazelDriver
    },
    "salesforce": {
        "name": "SalesForce",
        "driver": salesforceDriver
    },
    "scylladb": {
        "name": "ScyllaDB",
        "driver": scylladbDriver
    },
    "bigquery": {
        "name": "BigQuery",
        "driver": bigqueryDriver
    }
}

__all__ = ('BaseDriver', 'SUPPORTED', )
