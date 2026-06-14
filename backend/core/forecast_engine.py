# backend/core/forecast_engine.py
"""
Reserved for forecast model abstractions.

Currently the forecast logic lives directly in the endpoint
(backend/api/endpoints/forecast.py) and uses Prophet. When we add multiple
forecasting backends (e.g., StatsForecast, NeuralProphet, Holt-Winters),
this is where the strategy pattern goes.

Planned interface (rough sketch):

    class ForecastEngine(Protocol):
        def fit(self, df: pd.DataFrame) -> None: ...
        def predict(self, periods: int, freq: str) -> pd.DataFrame: ...

    class ProphetEngine(ForecastEngine): ...
    class StatsForecastEngine(ForecastEngine): ...
"""
