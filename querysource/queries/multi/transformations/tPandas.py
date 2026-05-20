from typing import Union
from abc import abstractmethod
from pandas import DataFrame
from ....exceptions import (
    DriverError,
    QueryException,
    DataNotFound
)
from .abstract import AbstractTransform


class tPandas(AbstractTransform):
    """Abstract base for Pandas-backed data transformations in a MultiQuery pipeline.

    Provides a standard lifecycle (``start``/``run``/``close``) and delegates
    the actual transformation to the abstract ``_run()`` method. Concrete subclasses
    implement ``_run()`` to apply specific pandas operations (sort, crosstab, pivot, etc.).

    Usage: Subclass ``tPandas`` to implement custom DataFrame transformations.
    End-users use concrete subclasses such as ``tOrder``, ``correlation``,
    ``crosstab``, ``pivot``, ``Forecast``, and ``Map``.

    Attributes:
        type: Transformation sub-type hint used by some subclasses.
        condition: Optional filter condition applied before the transformation.
        pd_args: Dict of extra keyword arguments passed through to the underlying
            pandas operation, e.g. ``{"sort": false, "dropna": true}``.

    Example:
        {
            "Transform": [
                {"tOrder": {"column": "revenue", "ascending": false}}
            ]
        }
    """  # noqa
    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
        """Init Method."""
        self.type: str = None
        self.condition: str = ''
        # Pandas Arguments:
        self.pd_args = kwargs.pop("pd_args", {})
        super(tPandas, self).__init__(data, **kwargs)

    @abstractmethod
    async def _run(self) -> DataFrame:
        """
        Abstract method to run the transformation.
        Returns:
            DataFrame: The transformed DataFrame.
        """
        pass  # pragma: no cover"""

    async def run(self):
        await self.start()
        try:
            df = await self._run()
            if df.empty:
                raise DataNotFound(
                    f"Data not Found over {self.__class__.__name__}"
                )
            return df
        except DataNotFound:
            raise
        except (ValueError, KeyError) as err:
            raise DriverError(
                f"{self.__class__.__name__} Error: {err!s}"
            ) from err
        except Exception as err:
            raise QueryException(
                f"{self.__class__.__name__} Exception {err!s}"
            ) from err
