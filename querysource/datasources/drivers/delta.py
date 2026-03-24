"""DeltaTable Datasource Driver.

Datasource driver for DeltaTable (Delta Lake) sources using asyncdb's delta driver.
"""
from typing import Optional
from datamodel import Column
from .abstract import BaseDriver


def delta_properties() -> tuple:
    return ('path', 'mode', 'storage_options')


class deltaDriver(BaseDriver):
    """DeltaTable Datasource Driver.

    Attributes:
        driver: asyncdb driver name ('delta').
        path: Filesystem or cloud path to the Delta table.
        mode: Default write mode (append, overwrite, error, ignore).
        storage_options: Cloud storage options (S3, GCS, Azure credentials).
    """
    driver: str = 'delta'
    driver_type: str = 'asyncdb'
    path: str = Column(required=True)
    mode: str = Column(required=False, default='append')
    storage_options: Optional[dict] = Column(required=False, default_factory=dict)
    required_properties: Optional[tuple] = Column(
        repr=False,
        default=delta_properties()
    )

    def params(self) -> dict:
        """Return params required for asyncdb delta driver.

        Returns:
            dict: Connection parameters for the delta driver.
        """
        p = {
            "path": self.path,
        }
        if self.storage_options:
            p["storage_options"] = self.storage_options
        return p

    def get_parameters(self) -> dict:
        """Return display parameters.

        Returns:
            dict: Parameters for display/logging.
        """
        return {
            "path": self.path,
            "mode": self.mode,
        }
