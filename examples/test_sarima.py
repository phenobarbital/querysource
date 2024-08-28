from pmdarima import auto_arima
from statsmodels.tsa.statespace.sarimax import SARIMAX
import pandas as pd

data = {
    'Period': ['2023-03-31', '2023-04-30', '2023-05-31', '2023-06-30', '2023-07-31',
               '2023-08-31', '2023-09-30', '2023-10-31', '2023-11-30', '2023-12-31',
               '2024-01-31', '2024-02-29', '2024-03-31'],
    'EBITDA': [20997.51, 166766.74, 89400.33, 190602.38, 87921.49, 144251.45,
               142293.08, 155590.12, -173699.07, -35095.42, 2156.94, -69645.70, -14255.82]
}

df = pd.DataFrame(data)
df['Period'] = pd.to_datetime(df['Period'])
df.set_index('Period', inplace=True)

try:
    model = auto_arima(df['EBITDA'], seasonal=True, m=12, trace=True, error_action='ignore',
                       suppress_warnings=True, stepwise=True, n_fits=50,
                       seasonal_test='ch', stationary=False)
    sarima_model = SARIMAX(df['EBITDA'].diff().dropna(), order=(1,0,1), seasonal_order=(1,0,1,12))
    sarima_model_fit = sarima_model.fit(disp=False)
    print("Model Fit Successfully!")
    forecast = sarima_model_fit.forecast(steps=6)
    print("Forecast:", forecast)
except Exception as e:
    print("Error in forecasting:", e)
