"""
Components.

This module contains the abstract class for components.

Components are optional building blocks of a multi-query.
They are responsible for making complex transformations and operations not covered by operators.

Note: AbstractComponent inherits from AbstractMulti, which provides the async context manager,
shared lifecycle methods (start, close), and introspection classmethods for documentation.
"""
from abc import abstractmethod
from ..abstract import AbstractMulti


class AbstractComponent(AbstractMulti):
    """AbstractComponent.

    Abstract Class for Multi-Query Components.

    Usage: Base class for optional complex transformation/operation steps in a MultiQuery pipeline.
    """

    _category = "Components"

    def __init__(self, data: dict, **kwargs) -> None:
        super().__init__(data, **kwargs)

    async def start(self):
        """Start the Component, useful for making validations before execution.

        Override in subclasses for pre-run validation. No-op by default (inherited from AbstractMulti).
        """

    @abstractmethod
    async def run(self):
        """Run the Component.
        """
