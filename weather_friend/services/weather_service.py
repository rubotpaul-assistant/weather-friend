"""Fetch current conditions and complete daily forecast extrema."""

import logging

import httpx

from weather_friend.models.weather import WeatherData

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
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
            self._daily_forecast_params(lat=self.lat, lon=self.lon),
            url=FORECAST_URL,
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
        data = await self._fetch_location(location)
        city = str(data.get("name") or location)
        forecast = await self._fetch(
            self._daily_forecast_params_for_location(data),
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
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Weather provider returned HTTP %s", exc.response.status_code
            )
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

    @staticmethod
    def _daily_forecast_params(*, lat: float, lon: float) -> dict[str, str | float]:
        """Build Open-Meteo daily forecast parameters for exact coordinates."""
        return {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 1.0,
        }

    @staticmethod
    def _daily_forecast_params_for_location(
        current: dict,
    ) -> dict[str, str | float]:
        """Build daily parameters from OpenWeatherMap-resolved coordinates."""
        coordinates = current.get("coord")
        if isinstance(coordinates, dict) and {
            "lat",
            "lon",
        }.issubset(coordinates):
            return WeatherService._daily_forecast_params(
                lat=float(coordinates["lat"]), lon=float(coordinates["lon"])
            )
        msg = "weather provider response omitted coordinates"
        raise ValueError(msg)

    async def _fetch_location(self, location: str) -> dict:
        """Resolve a location, retrying US state syntax only after a 404."""
        last_not_found: httpx.HTTPStatusError | None = None
        for provider_location in self._location_candidates(location):
            params: dict[str, str | float] = {
                "q": provider_location,
                "appid": self._api_key,
                "units": "imperial",
            }
            try:
                return await self._fetch(params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != httpx.codes.NOT_FOUND:
                    raise
                last_not_found = exc
        msg = f"unknown location: {location}"
        raise ValueError(msg) from last_not_found

    @staticmethod
    def _location_candidates(location: str) -> tuple[str, ...]:
        """Return the original query followed by a possible US-state query."""
        parts = [part.strip() for part in location.split(",")]
        if len(parts) == 2 and parts[1].upper() in _US_STATE_CODES:
            return location, f"{parts[0]},{parts[1].upper()},US"
        return (location,)

    @staticmethod
    def _daily_extrema(current: dict, forecast: dict) -> tuple[float, float]:
        """Read complete local-day extrema from Open-Meteo's daily response."""
        del current
        daily = forecast["daily"]
        highs = daily["temperature_2m_max"]
        lows = daily["temperature_2m_min"]
        if not highs or not lows:
            msg = "daily forecast response contained no temperature extrema"
            raise ValueError(msg)
        return float(highs[0]), float(lows[0])
