from typing import Union
import pandas as pd
from pmdarima import auto_arima
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.seasonal import seasonal_decompose
from ....exceptions import (
    DriverError,
    QueryException
)
from .abstract import AbstractTransform


class Forecast(AbstractTransform):
    def __init__(self, data: Union[dict, pd.DataFrame], **kwargs) -> None:
        self.reset_index: bool = bool(kwargs.pop('reset_index', True))
        self._order = tuple(kwargs.pop('order', [1, 1, 1]))
        self._steps: int = kwargs.pop('steps', 6)
        self._freq: str = kwargs.pop('frequency', 'ME')
        self.model_args: dict = kwargs.pop('model_args', {})

        super(Forecast, self).__init__(data, **kwargs)
        if not hasattr(self, 'index_column'):
            raise DriverError(
                "Forecast Transform: Missing Index on definition"
            )
        if not hasattr(self, 'columns'):
            raise DriverError(
                "Forecast Transform: Missing Columns on definition"
            )

    def arima_forecast(self):
        # Calculate the new index for the forecast period
        last_index = self.data.index[-1]
        forecast_index = pd.date_range(
            start=last_index,
            periods=self._steps + 1,
            freq=self._freq
        )[1:]  # Start from next period

        # Fit ARIMA Model for every column
        for column in self.columns:
            try:
                model = ARIMA(self.data[column], order=self._order)
                model_fit = model.fit()
                forecast = model_fit.forecast(steps=self._steps)

                # Create a series from the forecast with the new index
                forecast_series = pd.Series(forecast, index=forecast_index)

                # Append the forecast series to the data
                self.data = pd.concat(
                    [self.data, forecast_series.rename(f'{column}_forecast')],
                    axis=1
                )
            except Exception as err:
                raise QueryException(
                    f'Forecast Error: {err!s}'
                ) from err
        return self.data

    def forecast_sarima(self, data):
        try:
            data.index = pd.DatetimeIndex(data.index).to_period('M')
            model = auto_arima(
                data, seasonal=True, m=12, trace=False,
                error_action='ignore', suppress_warnings=True,
                stepwise=True, n_fits=50
            )
            sarima_model = SARIMAX(
                data,
                order=self._order,
                seasonal_order=(1, 1, 1, 12),
                initialization='approximate_diffuse'
            )
            fit = sarima_model.fit(disp=0)
            forecast = fit.get_forecast(steps=self._steps)
            return forecast.summary_frame()
        except Exception as e:
            print(f"Error in forecasting: {e}")
            return pd.Series(
                index=pd.date_range(
                    start=data.index[-1], periods=self._steps + 1, freq=self._freq
                )[1:]
            )

    def sarima_forecast(self):
        # Ensure the DataFrame includes forecasts without dropping existing data
        forecast_results = []
        programs = self.data[self.by_group].unique()

        for program in programs:
            program_data = self.data[self.data[self.by_group] == program]
            last_known_date = program_data.index.max()

            for column in self.columns:
                forecast_df = self.forecast_sarima(program_data[column])
                forecast_df[self.index_column] = pd.date_range(
                    start=last_known_date,
                    periods=self._steps + 1, freq=self._freq
                )[1:]
                forecast_df[self.by_group] = program

                forecast_df.rename(columns={'mean': column}, inplace=True)
                forecast_df.set_index(self.index_column, inplace=True)

                forecast_results.append(forecast_df[[column, self.by_group]])
        # Concatenate all forecast DataFrames
        forecast_final = pd.concat(forecast_results)
        print('Combined Forecast Data:', forecast_final.head())
        forecast_final = forecast_final.sort_index().ffill()

        # Merge with original data
        extended_data = pd.concat([self.data, forecast_final])
        return extended_data

    def exponential_smoothing(self, alpha: float = 0.2, **kwargs):
        # Set 'period' as index and convert to datetime
        self.data.dropna(subset=[self.index_column], inplace=True)

        forecast_df = pd.DataFrame()
        # Group by Program first
        for program, program_df in self.data.groupby(self.by_group):
            program_df = program_df.set_index(self.index_column)
            program_df.index = pd.to_datetime(program_df.index)

            # Resample only numeric columns in self.columns
            program_df[self.columns] = program_df[self.columns].resample('MS').ffill()

            # Set frequency
            # program_df.index.freq = 'MS'
            for col in self.columns:
                train = program_df[col].dropna()

                # Ensure enough data points
                if len(train) < 2 * self._steps:
                    print(f"Not enough data for {program} - {col}")
                    continue

                # Create the Exponential Smoothing model with the correct parameters
                model = ExponentialSmoothing(
                    train,
                    trend='add',
                    seasonal='add',
                    seasonal_periods=self._steps,
                    damped_trend=True,  # Use damped_trend instead of damped
                    use_boxcox=False,  # Set use_boxcox during initialization
                    initialization_method='estimated'
                )

                # Fit the model
                hw_model = model.fit(optimized=True, remove_bias=True)

                # Ensure the forecast starts after the last date in the training dataset
                last_date = train.index[-1]
                future_dates = pd.date_range(
                    start=last_date + pd.DateOffset(months=1),
                    periods=self._steps,
                    freq='MS'
                )

                # Predict for the defined number of steps ahead
                pred = hw_model.forecast(self._steps)
                pred.index = future_dates  # Assign the correct future dates to the forecasted data

                # Append predictions as a new DataFrame formatted similarly to the original data
                temp_df = pd.DataFrame({col: pred, self.by_group: program}, index=pred.index)
                forecast_df = pd.concat([forecast_df, temp_df], axis=0)

        # Set index to 'Period' column for merging
        forecast_df[self.index_column] = forecast_df.index
        forecast_df.reset_index(drop=True, inplace=True)
        # Combine and aggregate forecast data by Period and Program
        forecast_df = forecast_df.groupby(
            [self.index_column, self.by_group]
        ).agg('first').reset_index()

        # Combine original and forecast data
        combined_data = pd.concat([self.data, forecast_df], axis=0, ignore_index=True)
        combined_data.sort_values(by=[self.index_column, self.by_group], inplace=True)
        return combined_data

    async def run(self):
        await self.start()
        if self.model == 'ARIMA':
            df = self.arima_forecast()
        elif self.model == 'SARIMA':
            df = self.sarima_forecast()
        elif self.model == 'Exponential':
            df = self.exponential_smoothing(**self.model_args)
        try:
            if self.reset_index is True:
                df.reset_index(inplace=True)
            # Final Sorting:
            # df.sort_values(by=[self.by_group, self.index_column], inplace=True)
            df[self.index_column] = pd.to_datetime(df[self.index_column])
            print(df[self.index_column].dtype)  # Check after operation
            self.colum_info(df)
            return df
        except (ValueError, KeyError) as err:
            raise QueryException(
                f'Crosstab Error: {err!s}'
            ) from err
        except Exception as err:
            raise QueryException(
                f"Unknown error {err!s}"
            ) from err
