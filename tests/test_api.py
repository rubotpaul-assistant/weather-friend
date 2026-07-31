"""Tests for weather_friend.api module."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from weather_friend.api import build_app, start_api
from weather_friend.auth_middleware import SECRET_ENV_VAR, mint_token
from weather_friend.models.weather import WeatherData

TEST_SECRET = "test-shared-secret"
NARRATIVE = "The winds favor a light jacket today."


@pytest.fixture(autouse=True)
def shared_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set RUBOTPAUL_SHARED_SECRET in the environment for every test."""
    monkeypatch.setenv(SECRET_ENV_VAR, TEST_SECRET)
    return TEST_SECRET


@pytest.fixture()
def weather_service(sample_weather: WeatherData) -> MagicMock:
    """Create a WeatherService double returning sample weather."""
    service = MagicMock()
    service.city = "San Jose"
    service.get_current_weather = AsyncMock(return_value=sample_weather)
    service.get_weather_for_location = AsyncMock(return_value=sample_weather)
    return service


@pytest.fixture()
def message_service() -> MagicMock:
    """Create a MessageService double returning a canned narrative."""
    service = MagicMock()
    service.generate_forecast_message = AsyncMock(return_value=NARRATIVE)
    return service


def _auth_headers() -> dict[str, str]:
    """Build a valid Authorization header for the test caller."""
    return {"Authorization": f"Bearer {mint_token('rubotpaul')}"}


@asynccontextmanager
async def _api_client(
    weather_service: MagicMock, message_service: MagicMock
) -> AsyncIterator[TestClient]:
    """Start an in-process test client for the API app."""
    client = TestClient(TestServer(build_app(weather_service, message_service)))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


class TestHealthz:
    """Tests for the unauthenticated health check."""

    @pytest.mark.asyncio()
    async def test_healthz_ok_without_auth(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that /healthz responds 200 without a bearer token."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get("/healthz")

            assert resp.status == 200
            assert await resp.json() == {"ok": True}


class TestWeatherData:
    """Tests for GET /api/v1/weather/data."""

    @pytest.mark.asyncio()
    async def test_requires_auth(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a request without a bearer token gets 401."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get("/api/v1/weather/data?location=Portland,OR")

            assert resp.status == 401
            weather_service.get_weather_for_location.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_rejects_bad_token(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a garbage bearer token gets 401."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get(
                "/api/v1/weather/data?location=Portland,OR",
                headers={"Authorization": "Bearer not.a.token"},
            )

            assert resp.status == 401

    @pytest.mark.asyncio()
    async def test_happy_path(
        self,
        weather_service: MagicMock,
        message_service: MagicMock,
        sample_weather: WeatherData,
    ) -> None:
        """Test that an authed request returns the serialized weather."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get(
                "/api/v1/weather/data?location=San Jose,CA",
                headers=_auth_headers(),
            )

            assert resp.status == 200
            body = await resp.json()

        assert body == {
            "location": "San Jose,CA",
            "city": sample_weather.city,
            "temp_f": sample_weather.temp_f,
            "feels_like_f": sample_weather.feels_like_f,
            "high_f": sample_weather.high_f,
            "low_f": sample_weather.low_f,
            "humidity": sample_weather.humidity,
            "wind_speed_mph": sample_weather.wind_speed_mph,
            "description": sample_weather.description,
            "icon": sample_weather.icon,
        }
        weather_service.get_weather_for_location.assert_awaited_once_with("San Jose,CA")

    @pytest.mark.asyncio()
    async def test_missing_location(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a missing location uses configured coordinates."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get("/api/v1/weather/data", headers=_auth_headers())

            assert resp.status == 200
            body = await resp.json()

        assert body["location"] == "San Jose"
        weather_service.get_current_weather.assert_awaited_once_with()
        weather_service.get_weather_for_location.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_unknown_location(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a ValueError from the weather service gets 400."""
        weather_service.get_weather_for_location.side_effect = ValueError(
            "unknown location: Atlantis"
        )

        async with _api_client(weather_service, message_service) as client:
            resp = await client.get(
                "/api/v1/weather/data?location=Atlantis",
                headers=_auth_headers(),
            )

            assert resp.status == 400
            body = await resp.json()

        assert body == {"error": "unknown location: Atlantis"}


class TestWeatherForecast:
    """Tests for GET /api/v1/weather/forecast."""

    @pytest.mark.asyncio()
    async def test_requires_auth(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a request without a bearer token gets 401."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get("/api/v1/weather/forecast?location=Portland,OR")

            assert resp.status == 401
            message_service.generate_forecast_message.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_happy_path_includes_narrative(
        self,
        weather_service: MagicMock,
        message_service: MagicMock,
        sample_weather: WeatherData,
    ) -> None:
        """Test that the forecast payload adds the generated narrative."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get(
                "/api/v1/weather/forecast?location=San Jose,CA",
                headers=_auth_headers(),
            )

            assert resp.status == 200
            body = await resp.json()

        assert body["narrative"] == NARRATIVE
        assert body["city"] == sample_weather.city
        assert body["temp_f"] == sample_weather.temp_f
        message_service.generate_forecast_message.assert_awaited_once_with(
            sample_weather
        )

    @pytest.mark.asyncio()
    async def test_missing_location(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a missing location uses the configured default."""
        async with _api_client(weather_service, message_service) as client:
            resp = await client.get("/api/v1/weather/forecast", headers=_auth_headers())

            assert resp.status == 200
            body = await resp.json()

        assert body["location"] == "San Jose"
        assert body["narrative"] == NARRATIVE
        weather_service.get_current_weather.assert_awaited_once_with()

    @pytest.mark.asyncio()
    async def test_unknown_location_skips_narrative(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that a bad location 400s without calling the LLM."""
        weather_service.get_weather_for_location.side_effect = ValueError(
            "unknown location: Atlantis"
        )

        async with _api_client(weather_service, message_service) as client:
            resp = await client.get(
                "/api/v1/weather/forecast?location=Atlantis",
                headers=_auth_headers(),
            )

            assert resp.status == 400
            message_service.generate_forecast_message.assert_not_awaited()


class TestStartApi:
    """Tests for the start_api runner helper."""

    @pytest.mark.asyncio()
    async def test_serves_requests_until_cleanup(
        self, weather_service: MagicMock, message_service: MagicMock
    ) -> None:
        """Test that start_api binds a socket and serves the app."""
        runner = await start_api(
            weather_service, message_service, host="127.0.0.1", port=0
        )
        try:
            port = runner.addresses[0][1]
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:{port}/healthz")

            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        finally:
            await runner.cleanup()
