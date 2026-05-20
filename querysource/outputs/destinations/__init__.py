"""
querysource.outputs.destinations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

MultiQuery destination components.

This package exports the :data:`DESTINATION_REGISTRY` dict that maps YAML
step-names to destination classes, and the :func:`get_destination` factory
function for safe registry lookups.

New destinations register themselves by importing their class and adding an
entry to ``DESTINATION_REGISTRY`` at the bottom of this file.
"""
import logging as _logging
from typing import Union
import pandas as pd
from ..tables import TableOutput
from ...exceptions import OutputError
from .abstract import AbstractDestination

_pkg_logger = _logging.getLogger(__name__)


class TableOutputAdapter(AbstractDestination):
    """
    Thin adapter that wraps the existing :class:`~querysource.outputs.tables.TableOutput`
    so it can be registered in :data:`DESTINATION_REGISTRY` alongside the new
    destination classes.

    All constructor arguments are forwarded verbatim to :class:`TableOutput`.
    """

    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        # Call AbstractDestination.__init__ to set self.data and self.logger.
        super().__init__(data, **kwargs)
        # Build the wrapped TableOutput instance.
        self._table_output = TableOutput(data=data, **kwargs)

    async def run(self) -> Union[dict, pd.DataFrame]:
        """Delegate entirely to the wrapped :class:`TableOutput`."""
        result = await self._table_output.run()
        self.data = result
        return result

    async def close(self) -> None:
        """No-op — TableOutput closes its engine inside :meth:`run`."""


# ---------------------------------------------------------------------------
# Destination Registry
# ---------------------------------------------------------------------------
# Maps YAML step-names to destination classes.
# TableOutput is registered under both conventional spellings for backward
# compatibility with existing MultiQuery YAML configs.
DESTINATION_REGISTRY: dict[str, type[AbstractDestination]] = {
    "tableOutput": TableOutputAdapter,
    "TableOutput": TableOutputAdapter,
}

# New destinations are imported and registered below as their tasks complete.
# (Each destination module appends its entry here.)
try:
    from .sharepoint import ToSharepoint
    DESTINATION_REGISTRY["ToSharepoint"] = ToSharepoint
except ImportError:
    _pkg_logger.debug(
        "ToSharepoint destination not available: msgraph-sdk or azure-identity not installed"
    )

try:
    from .s3 import ToS3
    DESTINATION_REGISTRY["ToS3"] = ToS3
except ImportError:
    _pkg_logger.debug(
        "ToS3 destination not available: aioboto3 not installed"
    )

try:
    from .table import TableDestination
    DESTINATION_REGISTRY["Table"] = TableDestination
except ImportError:
    _pkg_logger.debug(
        "Table destination not available"
    )

try:
    from .dwh import DWHDestination
    DESTINATION_REGISTRY["DWH"] = DWHDestination
except ImportError:
    _pkg_logger.debug(
        "DWH destination not available"
    )


def get_destination(step_name: str) -> type[AbstractDestination]:
    """
    Return the destination class registered under *step_name*.

    :param step_name: The YAML step key (e.g. ``"ToSharepoint"``, ``"tableOutput"``).
    :returns: The destination class.
    :raises OutputError: If *step_name* is not registered.
    """
    cls = DESTINATION_REGISTRY.get(step_name)
    if cls is None:
        registered = ", ".join(sorted(DESTINATION_REGISTRY))
        raise OutputError(
            f"Unknown destination step '{step_name}'. "
            f"Registered destinations: {registered}"
        )
    return cls


__all__ = (
    "AbstractDestination",
    "TableOutputAdapter",
    "DESTINATION_REGISTRY",
    "get_destination",
)
