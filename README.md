# QuerySource #

QuerySource is a powerful Python library designed to streamline access to multiple databases and external APIs through a single, unified interface. Utilizing the proxy design pattern, QuerySource allows developers to seamlessly integrate various data sources into their projects without worrying about the complexities of managing multiple connections.

## Features ##

* Unified interface for querying multiple databases (Redis, PostgreSQL, MySQL, Oracle, SQL Server, InfluxDB, CouchDB, Druid)
* Support for external APIs (Salesforce, ShopperTrack, ZipCodeAPI, etc.)
* Easy-to-use API for executing queries
* Extensible design, allowing for easy addition of new data sources

## Installation

<div class="termy">

```console
$ pip install querysource
---> 100%
Successfully installed querysource
```


### Requirements ###

* Python >= 3.9
* asyncio (https://pypi.python.org/pypi/asyncio/)

## Basic Usage ##

QuerySource can be used in several ways, one is using QS object itself:

```python
from querysource.queries.qs import QS

query = QS(
    query='SELECT * FROM tests',
    output_format='pandas'
)
result, error = await query.query()
```

## Extending QuerySource ##

To add support for a new data source, create a new class inheriting from the BaseProvider or BaseAPI class and implement the required methods (prepare_connection, query, close). Then, register the new data source in the QuerySource class by adding it to the appropriate dictionary (_supported_databases or _supported_apis).


### Contributing ###

We welcome contributions to QuerySource! If you'd like to contribute, please follow these steps:

* Fork the repository
* Create a new branch for your feature or bugfix
* Implement your changes
* Add tests covering your changes
* Create a pull request against the original repository

### Contribution guidelines ###

* Writing tests
* Code review
* Other guidelines

### License ###

QuerySource is released under the BSD License. See the LICENSE file for more details.
