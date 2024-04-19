import asyncio
from typing import Union
from pandas import DataFrame
import pandas as pd
from navigator.actions.google.models import TravelerSearch, Location
from navigator.actions.google.maps import Route
from ..abstract import AbstractTransform

class GoogleMaps(AbstractTransform):

    def __init__(self, data: Union[dict, DataFrame], **kwargs) -> None:
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
        traveler = TravelerSearch(
            origin=origin,
            destination=destination,  # Get destination from last location
            locations=row['locations'],
            optimal=False,
            scale=2,
            zoom=10,
            map_size=(800, 800)
            # departure_time=row['departure_time']
        )
        try:
            route = Route()
            result = await route.waypoint_route(traveler)
            if result:
                for key, value in result.items():
                    df.at[idx, key] = value
                # Then, Calculate the "Optimal" Route:
                traveler.optimal = True
                result = await route.waypoint_route(traveler)
                for key, val in result.items():
                    col = f"opt_{key}"
                    df.at[idx, col] = val
        except Exception as exc:
            print(exc)
            pass

    async def run(self):
        await self.start()
        # Calculate the route and optimal route for every row in query:
        # result_df = self.data.apply(self.process_row, axis=1)
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
        # Test
        for idx, row in self.data.iterrows():
            await self.process_row(row, idx, df)
            await asyncio.sleep(1)
            print('Processed row:', idx)
        df.is_copy = False  # This line might not be necessary
        print(df)
        self.data = df
        return df
