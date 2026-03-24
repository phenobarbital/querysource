"""Apache Iceberg Datasource Driver.

Datasource driver for Apache Iceberg catalogs using asyncdb's iceberg driver.
"""
from typing import Optional
from dataclasses import InitVar
from datamodel import Column
from .abstract import BaseDriver


def iceberg_properties() -> tuple:
    return (
        'catalog_name', 'catalog_type', 'catalog_uri',
        'warehouse', 'namespace', 'token'
    )


class icebergDriver(BaseDriver):
    """Apache Iceberg Datasource Driver.

    Attributes:
        driver: asyncdb driver name ('iceberg').
        catalog_name: Name of the Iceberg catalog.
        catalog_type: Type of catalog (sql, rest, hive, glue, dynamodb).
        catalog_uri: URI of the catalog service.
        warehouse: Warehouse path for table storage.
        namespace: Default namespace for table operations.
        token: Authentication token (for REST catalogs).
    """
    driver: str = 'iceberg'
    driver_type: str = 'asyncdb'
    catalog_name: str = Column(required=False, default='default')
    catalog_type: str = Column(required=False, default='sql')
    catalog_uri: str = Column(required=False, default=None)
    warehouse: str = Column(required=False, default=None)
    namespace: str = Column(required=False, default='')
    token: str = Column(required=False, default=None)
    storage_options: Optional[dict] = Column(required=False, default_factory=dict)
    catalog_properties: Optional[dict] = Column(required=False, default_factory=dict)
    required_properties: Optional[tuple] = Column(
        repr=False,
        default=iceberg_properties()
    )

    def params(self) -> dict:
        """Return params required for asyncdb iceberg driver.

        Returns:
            dict: Connection parameters for the iceberg driver.
        """
        p = {
            "catalog_name": self.catalog_name,
            "catalog_type": self.catalog_type,
        }
        if self.catalog_uri:
            p["catalog_uri"] = self.catalog_uri
        if self.warehouse:
            p["warehouse"] = self.warehouse
        if self.namespace:
            p["namespace"] = self.namespace
        if self.token:
            p["token"] = self.token
        if self.storage_options:
            p["storage_options"] = self.storage_options
        if self.catalog_properties:
            p["catalog_properties"] = self.catalog_properties
        return p

    def get_parameters(self) -> dict:
        """Return display parameters.

        Returns:
            dict: Parameters for display/logging.
        """
        return {
            "catalog_name": self.catalog_name,
            "catalog_type": self.catalog_type,
            "namespace": self.namespace,
        }
