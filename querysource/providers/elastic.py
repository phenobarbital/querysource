"""Elasticsearch Provider.

QuerySource Provider for Elasticsearch queries using asyncdb elastic driver.
"""
from typing import Any, Optional, Union
from aiohttp import web
from asyncdb.drivers.elastic import elastic
from datamodel.parsers.json import json_encoder
from querysource.models import QueryModel
from .abstract import BaseProvider

try:
    from querysource.parsers.elastic import ElasticParser
except ImportError:
    from querysource.parsers.abstract import AbstractParser as ElasticParser


class elasticProvider(BaseProvider):
    """Elasticsearch Provider.

    QuerySource Provider for Elasticsearch queries.
    Uses asyncdb's elastic driver for async connections to Elasticsearch/OpenSearch.
    """
    __parser__ = ElasticParser
    _parser_options = {}

    def __init__(
        self,
        slug: str = None,
        query: Any = None,
        qstype: str = None,
        definition: Union[QueryModel, dict] = None,
        conditions: dict = None,
        request: web.Request = None,
        **kwargs
    ):
        super(elasticProvider, self).__init__(
            slug=slug,
            query=query,
            qstype=qstype,
            definition=definition,
            conditions=conditions,
            request=request,
            **kwargs
        )
        self.is_raw = False
        self._database: Optional[str] = None
        self._index: Optional[str] = None

        # Extract index/database from definition or conditions
        if self._definition is not None:
            self._database = getattr(self._definition, 'source', None)
        if not self._database and isinstance(self._conditions, dict):
            self._database = (
                self._conditions.get('database')
                or self._conditions.get('index')
                or self._conditions.get('schema')
            )
        if self._database:
            self._index = self._database

        # Determine driver type (elasticsearch vs opensearch)
        self._client_type = kwargs.get('client_type', 'auto')

    async def prepare_connection(self):
        """Prepare the Elasticsearch query and connection."""
        result = None
        error = None

        try:
            parser = self.__parser__(
                definition=self._definition,
                conditions=self._conditions,
                **self._parser_options
            )
            result = await parser.build_query(
                querylimit=self._querylimit,
                offset=self._offset
            )
            self._query = result
        except Exception as err:
            error = err
            self.logger.error(
                f"Elasticsearch prepare_connection Error: {err}"
            )

        return (result, error)

    async def columns(self):
        """Return the list of fields (columns) being queried."""
        if self._definition is not None and hasattr(self._definition, 'fields'):
            return getattr(self._definition, 'fields', [])
        return []

    async def dry_run(self):
        """Return the prepared query without executing."""
        result, error = await self.prepare_connection()
        if error:
            return (None, error)
        return (result, None)

    async def query(self):
        """Execute the Elasticsearch query and return results."""
        result = None
        error = None

        # Prepare the query
        query_body, prep_error = await self.prepare_connection()
        if prep_error:
            return (None, prep_error)

        try:
            # Get connection params from driver
            params = self._driver.params() if self._driver else {}
            if not params:
                params = self._get_default_params()

            # Extract index from the query body
            index = query_body.pop('index', self._index)
            if not index:
                raise ValueError(
                    "Elasticsearch Error: No index specified for query."
                )

            async with elastic(params=params) as conn:
                await conn.connection()

                if conn._connection is None:
                    raise ConnectionError(
                        "Elasticsearch Error: Unable to connect."
                    )

                try:
                    # Use the search API
                    response = await conn._connection.search(
                        index=index,
                        body=query_body
                    )

                    # Extract hits from the response
                    if isinstance(response, dict):
                        hits = response.get('hits', {})
                        result = [
                            hit.get('_source', hit)
                            for hit in hits.get('hits', [])
                        ]
                        # Attach total count as metadata
                        self._total = hits.get('total', {})
                        if isinstance(self._total, dict):
                            self._total = self._total.get('value', 0)
                    else:
                        result = response

                except Exception as err:
                    error = err
                    self.logger.error(
                        f"Elasticsearch Query Error: {err}"
                    )

        except Exception as err:
            error = err
            self.logger.error(
                f"Elasticsearch Connection Error: {err}"
            )

        return (result, error)

    def _get_default_params(self) -> dict:
        """Get default connection params from conf."""
        try:
            from querysource.conf import (
                ELASTIC_HOST,
                ELASTIC_PORT,
                ELASTIC_USER,
                ELASTIC_PASSWORD,
                ELASTIC_DATABASE,
                ELASTIC_PROTOCOL,
                ELASTIC_USE_SSL,
            )
            params = {
                'host': ELASTIC_HOST,
                'port': ELASTIC_PORT,
                'db': ELASTIC_DATABASE,
                'protocol': ELASTIC_PROTOCOL,
            }
            if ELASTIC_USER:
                params['user'] = ELASTIC_USER
                params['password'] = ELASTIC_PASSWORD
            if ELASTIC_USE_SSL:
                params['use_ssl'] = True
                params['verify_certs'] = False
            return params
        except ImportError:
            return {}

    async def close(self):
        """Close the provider connection."""
        try:
            if self._qs:
                await self._qs.close()
        except Exception:
            pass
        self._qs = None
