"""Fetches weather data from OpenWeatherMap API."""

import logging
from datetime import UTC, date, datetime, timedelta

import httpx

from weather_friend.models.weather import WeatherData

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"
_REQUEST_TIMEOUT = 10.0

_US_STATE_CODES = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
)


class WeatherService:
    """Service for retrieving current weather data from OpenWeatherMap.

    Attributes:
        lat: Latitude for the weather query.
        lon: Longitude for the weather query.
        city: Display name for the city.
    """

    def __init__(self, api_key: str, lat: float, lon: float, city: str) -> None:
        """Initialize the weather service.

        Args:
            api_key: OpenWeatherMap API key.
            lat: Latitude for the weather query.
            lon: Longitude for the weather query.
            city: Display name for the city.
        """
        self._api_key = api_key
        self.lat = lat
        self.lon = lon
        self.city = city

    async def get_current_weather(self) -> WeatherData:
        """Fetch current weather data for the configured coordinates.

        Returns:
            A WeatherData instance with the current conditions.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.
            httpx.RequestError: If the request fails due to network issues.
        """
        params: dict[str, str | float] = {
            "lat": self.lat,
            "lon": self.lon,
            "appid": self._api_key,
            "units": "imperial",
        }
        data = await self._fetch(params)
        forecast = await self._fetch(
            self._forecast_params(lat=self.lat, lon=self.lon), url=FORECAST_URL
        )
        high_f, low_f = self._daily_extrema(data, forecast)
        return self._build_weather_data(
            data, city=self.city, high_f=high_f, low_f=low_f
        )

    async def get_weather_for_location(self, location: str) -> WeatherData:
        """Fetch current weather data for an arbitrary location string.

        Args:
            location: Free-form location query, e.g. "Portland,OR".

        Returns:
            A WeatherData instance with the current conditions. The city
            name comes from the API response when available, otherwise
            the requested location string.

        Raises:
            ValueError: If the API does not recognize the location.
            httpx.HTTPStatusError: If the API returns any other error status.
            httpx.RequestError: If the request fails due to network issues.
        """
        provider_location = self._canonical_location(location)
        params: dict[str, str | float] = {
            "q": provider_location,
            "appid": self._api_key,
            "units": "imperial",
        }
        try:
            data = await self._fetch(params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                msg = f"unknown location: {location}"
                raise ValueError(msg) from exc
            raise
        city = str(data.get("name") or location)
        forecast = await self._fetch(
            self._forecast_params_for_location(data, provider_location),
            url=FORECAST_URL,
        )
        high_f, low_f = self._daily_extrema(data, forecast)
        return self._build_weather_data(data, city=city, high_f=high_f, low_f=low_f)

    async def _fetch(
        self, params: dict[str, str | float], *, url: str = BASE_URL
    ) -> dict:
        """Call the OpenWeatherMap API and return the parsed JSON body.

        Args:
            params: Query parameters for the current-weather endpoint.

        Returns:
            The decoded JSON response.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.
            httpx.RequestError: If the request fails due to network issues.
        """
        # OWM requires the API key as a query param (not a header);
        # this is an upstream API constraint. HTTPS is enforced by BASE_URL.
        try:
            async with httpx.AsyncClient(
                timeout=_REQUEST_TIMEOUT,
            ) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data: dict = response.json()
        except httpx.HTTPStatusError:
            logger.exception("Weather API HTTP error")
            raise
        except httpx.RequestError:
            logger.exception("Weather API request failed")
            raise
        return data

    @staticmethod
    def _build_weather_data(
        data: dict,
        *,
        city: str,
        high_f: float | None = None,
        low_f: float | None = None,
    ) -> WeatherData:
        """Map an OpenWeatherMap response body onto the WeatherData model.

        Args:
            data: Decoded current-weather JSON response.
            city: Display name to attach to the result.

        Returns:
            A populated WeatherData instance.
        """
        main = data["main"]
        weather = data["weather"][0]
        wind = data["wind"]

        return WeatherData(
            city=city,
            temp_f=float(main["temp"]),
            feels_like_f=float(main["feels_like"]),
            humidity=int(main["humidity"]),
            description=str(weather["description"]),
            wind_speed_mph=float(wind["speed"]),
            high_f=float(main["temp_max"] if high_f is None else high_f),
            low_f=float(main["temp_min"] if low_f is None else low_f),
            icon=str(weather["icon"]),
        )

    def _forecast_params(self, *, lat: float, lon: float) -> dict[str, str | float]:
        """Build forecast parameters for exact coordinates."""
        return {
            "lat": lat,
            "lon": lon,
            "appid": self._api_key,
            "units": "imperial",
        }

    def _forecast_params_for_location(
        self, current: dict, provider_location: str
    ) -> dict[str, str | float]:
        """Reuse resolved coordinates so current and forecast locations match."""
        coordinates = current.get("coord")
        if isinstance(coordinates, dict) and {
            "lat",
            "lon",
        }.issubset(coordinates):
            return self._forecast_params(
                lat=float(coordinates["lat"]), lon=float(coordinates["lon"])
            )
        return {
            "q": provider_location,
            "appid": self._api_key,
            "units": "imperial",
        }

    @staticmethod
    def _canonical_location(location: str) -> str:
        """Disambiguate OpenWeatherMap queries containing a US state code."""
        parts = [part.strip() for part in location.split(",")]
        if len(parts) == 2 and parts[1].upper() in _US_STATE_CODES:
            return f"{parts[0]},{parts[1].upper()},US"
        return location

    @staticmethod
    def _daily_extrema(current: dict, forecast: dict) -> tuple[float, float]:
        """Calculate extrema from forecast intervals in the local calendar day."""
        current_main = current["main"]
        highs = [float(current_main["temp_max"])]
        lows = [float(current_main["temp_min"])]
        timezone_offset = WeatherService._timezone_offset(forecast, current)
        current_day = WeatherService._local_date(
            int(current.get("dt", datetime.now(tz=UTC).timestamp())), timezone_offset
        )

        entries = forecast.get("list", [])
        if not isinstance(entries, list):
            return max(highs), min(lows)
        for entry in entries:
            if not isinstance(entry, dict) or "dt" not in entry:
                continue
            if (
                WeatherService._local_date(int(entry["dt"]), timezone_offset)
                != current_day
            ):
                continue
            main = entry.get("main")
            if not isinstance(main, dict):
                continue
            highs.append(float(main["temp_max"]))
            lows.append(float(main["temp_min"]))
        return max(highs), min(lows)

    @staticmethod
    def _timezone_offset(forecast: dict, current: dict) -> int:
        """Read the provider's UTC offset from either response shape."""
        city = forecast.get("city")
        if isinstance(city, dict) and "timezone" in city:
            return int(city["timezone"])
        return int(current.get("timezone", 0))

    @staticmethod
    def _local_date(timestamp: int, timezone_offset: int) -> date:
        """Convert a Unix timestamp to a date at a fixed UTC offset."""
        return (
            datetime.fromtimestamp(timestamp, tz=UTC)
            + timedelta(seconds=timezone_offset)
        ).date()
