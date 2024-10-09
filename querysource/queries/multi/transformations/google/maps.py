import asyncio
from typing import Union
from pandas import DataFrame
import numpy as np
import pandas as pd
from datamodel.exceptions import ValidationError
from navigator.actions.google.models import TravelerSearch, Location
from navigator.actions.google.maps import Route
from ..abstract import AbstractTransform

class GoogleMaps(AbstractTransform):

    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
        self.zoom: int = kwargs.get('zoom', 10)
        self.map_scale: int = kwargs.get('map_scale', 2)
        self.timestamp_key: str = kwargs.get('timestamp_key', 'timestamp')
        # self.map_size: tuple = kwargs.get('map_size', (800, 800))
        self.departure_time: str = kwargs.get('departure_time', None)
        super(GoogleMaps, self).__init__(data, **kwargs)
        if not hasattr(self, 'type'):
            self._type = 'get_route'

    async def process_row(self, row, idx, df):
        """Processes a single row of the DataFrame,
        calls the Google Maps API, and adds results.
        Args:
            row (pd.Series): A single row from the DataFrame.

        Returns:
            pd.Series: The modified row with additional columns from the API response.
        """
        # Create TravelerSearch instance
        origin = tuple(row['origin'])
        origin = Location(
            latitude=origin[0],
            longitude=origin[1]
        )
        destination = origin
        args = {}
        if self.departure_time:
            args = {
                "departure_time": self.departure_time
            }
        try:
            traveler = TravelerSearch(
                origin=origin,
                destination=destination,  # Get destination from last location
                locations=row['locations'],
                optimal=False,
                scale=self.map_scale,
                zoom=self.zoom,
                map_size=(800, 800),
                **args
            )
        except ValidationError as exc:
            self.logger.error(f'Error on validation : {exc}')
            return
        try:
            route = Route()
            result = await route.waypoint_route(
                traveler,
                add_overview=False,
                complete=False
            )
        except Exception as exc:
            self.logger.error(f"Error on route: {exc}")
            return
        if result:
            for key, value in result.items():
                if key not in df.columns and isinstance(value, (list, np.ndarray)):
                    df[key] = [[] for _ in range(len(df))]
                try:
                    df.at[idx, key] = value
                except ValueError as e:
                    self.logger.error(f"Map Error for key '{key}': {e}")
                    # Optionally continue to the next key if there's an error
                    continue
            # Then, Calculate the "Optimal" Route:
            try:
                traveler.optimal = True
                result = await route.waypoint_route(
                    traveler,
                    add_overview=False,
                    complete=False
                )
                for key, val in result.items():
                    col = f"opt_{key}"
                    if col not in df.columns and isinstance(value, (list, np.ndarray)):
                        df[col] = [[] for _ in range(len(df))]
                    try:
                        df.at[idx, col] = val
                    except ValueError as e:
                        self.logger.error(f"Map Error for key '{col}': {e}")
                        # Optionally continue to the next key if there's an error
                        continue
            except Exception as exc:
                self.logger.error(f"Error on optimal route: {exc}")
                return

    async def run(self):
        await self.start()
        # Calculate the route and optimal route for every row in query:
        if self.data.empty:
            return self.data
        df = self.data.copy()
        col_list = [
            "associate_oid",
            "visitor_name",
            "departure_time",
            "start_timestamp",
            "end_timestamp",
            "visit_date",
            "origin",
            "form_info",
            "locations",
            "route_legs",
            "route",
            "total_duration",
            "total_distance",
            "duration",
            "distance",
            "map_url",
            "map",
            "opt_route_legs",
            "opt_route",
            "opt_total_duration",
            "opt_total_distance",
            "opt_duration",
            "opt_distance",
            "opt_map_url",
            "opt_map",
            "first_leg_distance",
            "last_leg_distance",
            "opt_first_leg_distance",
            "opt_last_leg_distance"
        ]
        for col in col_list:
            if col not in df.columns:
                df[col] = pd.NA

        # First: sort locations by timestamp:
        def sort_by_timestamp(locations):
            if isinstance(locations, list):  # Ensure the value is a list
                return sorted(locations, key=lambda x: x[self.timestamp_key])
            return locations
        try:
            self.data['locations'] = self.data['locations'].apply(
                sort_by_timestamp
            )
        except KeyError as exc:
            self.logger.error(
                f"Error on sorting locations: {exc}"
            )
        for idx, row in self.data.iterrows():
            try:
                await self.process_row(row, idx, df)
                await asyncio.sleep(1)
                print(':: Processed row:', idx)
            except Exception as exc:
                self.logger.error(
                    f"Error Processing row {idx}: {exc}"
                )
                continue
        df.is_copy = False  # This line might not be necessary
        self.data = df
        return df
