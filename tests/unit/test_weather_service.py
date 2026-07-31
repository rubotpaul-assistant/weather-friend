"""Tests for weather_friend.services.weather_service module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from weather_friend.services.weather_service import WeatherService


def _make_service() -> WeatherService:
    """Create a WeatherService with fake credentials."""
    return WeatherService(
        api_key="fake-key", lat=37.3382, lon=-121.8863, city="San Jose"
    )


def _sample_api_response() -> dict:
    """Return a sample OpenWeatherMap API response."""
    return {
        "main": {
            "temp": 68.0,
            "feels_like": 65.0,
            "humidity": 55,
            "temp_max": 74.0,
            "temp_min": 58.0,
        },
        "weather": [{"description": "scattered clouds", "icon": "03d"}],
        "wind": {"speed": 8.0},
    }


def _mock_client(response: MagicMock) -> AsyncMock:
    """Create an async-context-manager httpx client double."""
    client = AsyncMock()
    client.get.return_value = response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _mock_response(payload: dict) -> MagicMock:
    """Create a successful httpx response double with a JSON payload."""
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


class TestWeatherService:
    """Tests for the WeatherService class."""

    def test_init_stores_params(self) -> None:
        """Test that constructor stores all parameters."""
        service = _make_service()
        assert service._api_key == "fake-key"
        assert service.lat == 37.3382
        assert service.lon == -121.8863
        assert service.city == "San Jose"

    def test_api_key_is_private(self) -> None:
        """Test that the API key is stored as a private attribute."""
        service = _make_service()
        assert not hasattr(service, "api_key")
        assert hasattr(service, "_api_key")

    @pytest.mark.asyncio()
    async def test_get_current_weather_success(self) -> None:
        """Test successful weather fetch returns correct WeatherData."""
        service = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = _sample_api_response()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            weather = await service.get_current_weather()

        assert weather.city == "San Jose"
        assert weather.temp_f == 68.0
        assert weather.feels_like_f == 65.0
        assert weather.humidity == 55
        assert weather.description == "scattered clouds"
        assert weather.wind_speed_mph == 8.0
        assert weather.high_f == 74.0
        assert weather.low_f == 58.0
        assert weather.icon == "03d"

    @pytest.mark.asyncio()
    async def test_get_current_weather_sends_correct_params(self) -> None:
        """Test that the API call includes correct query parameters."""
        service = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = _sample_api_response()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await service.get_current_weather()

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["lat"] == 37.3382
        assert params["lon"] == -121.8863
        assert params["appid"] == "fake-key"
        assert params["units"] == "imperial"

    @pytest.mark.asyncio()
    async def test_get_current_weather_uses_forecast_daily_extrema(self) -> None:
        """Test that daily highs and lows come from local-day forecasts."""
        current = _sample_api_response()
        current.update({"dt": 1_722_532_800, "coord": {"lat": 37.3, "lon": -121.9}})
        forecast = {
            "city": {"timezone": -25_200},
            "list": [
                {
                    "dt": 1_722_535_600,
                    "main": {"temp_min": 61.0, "temp_max": 88.0},
                },
                {
                    "dt": 1_722_546_400,
                    "main": {"temp_min": 64.0, "temp_max": 84.0},
                },
                {
                    "dt": 1_722_621_600,
                    "main": {"temp_min": 58.0, "temp_max": 79.0},
                },
            ],
        }
        mock_client = AsyncMock()
        mock_client.get.side_effect = [
            _mock_response(current),
            _mock_response(forecast),
        ]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            weather = await _make_service().get_current_weather()

        assert weather.high_f == 88.0
        assert weather.low_f == 58.0
        forecast_params = mock_client.get.call_args_list[1].kwargs["params"]
        assert forecast_params["lat"] == 37.3382
        assert forecast_params["lon"] == -121.8863

    @pytest.mark.asyncio()
    async def test_get_current_weather_uses_explicit_timeout(self) -> None:
        """Test that the httpx client is created with an explicit timeout."""
        service = _make_service()
        mock_response = MagicMock()
        mock_response.json.return_value = _sample_api_response()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ) as mock_constructor:
            await service.get_current_weather()

        assert mock_constructor.call_count == 2
        mock_constructor.assert_called_with(timeout=10.0)

    @pytest.mark.asyncio()
    async def test_get_current_weather_http_error(self) -> None:
        """Test that HTTP errors are re-raised."""
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "weather_friend.services.weather_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await service.get_current_weather()

    @pytest.mark.asyncio()
    async def test_get_current_weather_request_error(self) -> None:
        """Test that network errors are re-raised."""
        service = _make_service()

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.RequestError("Connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "weather_friend.services.weather_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(httpx.RequestError),
        ):
            await service.get_current_weather()


class TestGetWeatherForLocation:
    """Tests for WeatherService.get_weather_for_location."""

    @pytest.mark.asyncio()
    async def test_success_uses_response_city_name(self) -> None:
        """Test that a location fetch returns data named after the API city."""
        service = _make_service()
        payload = _sample_api_response()
        payload["name"] = "Portland"
        mock_client = _mock_client(_mock_response(payload))

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            weather = await service.get_weather_for_location("Portland,OR")

        assert weather.city == "Portland"
        assert weather.temp_f == 68.0
        assert weather.icon == "03d"

    @pytest.mark.asyncio()
    async def test_sends_location_query_params(self) -> None:
        """Test that the API call queries by location string."""
        service = _make_service()
        mock_client = _mock_client(_mock_response(_sample_api_response()))

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await service.get_weather_for_location("San Jose,CA")

        params = mock_client.get.call_args_list[0].kwargs["params"]
        assert params["q"] == "San Jose,CA,US"
        assert params["appid"] == "fake-key"
        assert params["units"] == "imperial"
        assert "lat" not in params
        assert "lon" not in params

    @pytest.mark.asyncio()
    async def test_missing_name_falls_back_to_requested_location(self) -> None:
        """Test that the requested location is used when the API omits name."""
        service = _make_service()
        mock_client = _mock_client(_mock_response(_sample_api_response()))

        with patch(
            "weather_friend.services.weather_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            weather = await service.get_weather_for_location("Portland,OR")

        assert weather.city == "Portland,OR"

    @pytest.mark.asyncio()
    async def test_unknown_location_raises_value_error(self) -> None:
        """Test that an upstream 404 becomes a ValueError."""
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
        mock_client = _mock_client(mock_response)

        with (
            patch(
                "weather_friend.services.weather_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(ValueError, match="unknown location: Atlantis"),
        ):
            await service.get_weather_for_location("Atlantis")

    @pytest.mark.asyncio()
    async def test_non_404_http_error_is_reraised(self) -> None:
        """Test that non-404 HTTP errors propagate unchanged."""
        service = _make_service()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        mock_client = _mock_client(mock_response)

        with (
            patch(
                "weather_friend.services.weather_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await service.get_weather_for_location("Portland,OR")

    @pytest.mark.asyncio()
    async def test_request_error_is_reraised(self) -> None:
        """Test that network errors propagate unchanged."""
        service = _make_service()
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.RequestError("Connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "weather_friend.services.weather_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(httpx.RequestError),
        ):
            await service.get_weather_for_location("Portland,OR")
