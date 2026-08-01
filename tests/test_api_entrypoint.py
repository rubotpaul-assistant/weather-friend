"""Tests for the standalone API entrypoint and its env configuration."""

import asyncio
import os
import signal
import socket
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import aiohttp
import pytest

from weather_friend.api import main, serve
from weather_friend.auth_middleware import SECRET_ENV_VAR
from weather_friend.config import (
    API_PORT_ENV_VAR,
    CITY_NAME_ENV_VAR,
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    LATITUDE_ENV_VAR,
    LONGITUDE_ENV_VAR,
    ApiSettings,
)

TEST_SECRET = "test-shared-secret"


@pytest.fixture(autouse=True)
def api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide the standalone API's required environment variables."""
    monkeypatch.setenv("OPENWEATHER_API_KEY", "fake-weather-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")
    monkeypatch.setenv(SECRET_ENV_VAR, TEST_SECRET)
    monkeypatch.delenv(API_PORT_ENV_VAR, raising=False)
    monkeypatch.delenv(LATITUDE_ENV_VAR, raising=False)
    monkeypatch.delenv(LONGITUDE_ENV_VAR, raising=False)
    monkeypatch.delenv(CITY_NAME_ENV_VAR, raising=False)


def _free_port() -> int:
    """Reserve an ephemeral localhost port for a test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _settings(port: int) -> ApiSettings:
    """Build ApiSettings bound to localhost on the given port."""
    return ApiSettings(
        openweather_api_key="fake-weather-key",
        anthropic_api_key="fake-anthropic-key",
        port=port,
    )


async def _wait_for_healthz(port: int) -> dict[str, Any]:
    """Poll /healthz until the API responds, returning its JSON body."""
    async with aiohttp.ClientSession() as session:
        for _ in range(50):
            try:
                url = f"http://127.0.0.1:{port}/healthz"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        body: dict[str, Any] = await resp.json()
                        return body
            except aiohttp.ClientConnectionError:
                await asyncio.sleep(0.1)
    raise AssertionError("healthz never responded")


async def _healthz_refused(port: int) -> bool:
    """Report whether /healthz now refuses connections."""
    async with aiohttp.ClientSession() as session:
        try:
            url = f"http://127.0.0.1:{port}/healthz"
            async with session.get(url):
                return False
        except aiohttp.ClientConnectionError:
            return True


class TestApiSettings:
    """Tests for ApiSettings.from_env."""

    def test_from_env_defaults(self) -> None:
        """Test that from_env fills defaults when only keys are set."""
        settings = ApiSettings.from_env()

        assert settings.openweather_api_key == "fake-weather-key"
        assert settings.anthropic_api_key == "fake-anthropic-key"
        assert settings.host == DEFAULT_API_HOST == "127.0.0.1"
        assert settings.port == DEFAULT_API_PORT == 8002
        assert settings.city_name == "San Jose"

    def test_from_env_custom_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that WEATHER_FRIEND_API_PORT overrides the default port."""
        monkeypatch.setenv(API_PORT_ENV_VAR, "9010")

        assert ApiSettings.from_env().port == 9010

    def test_from_env_custom_location(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that location environment variables override San Jose."""
        monkeypatch.setenv(LATITUDE_ENV_VAR, "32.7157")
        monkeypatch.setenv(LONGITUDE_ENV_VAR, "-117.1611")
        monkeypatch.setenv(CITY_NAME_ENV_VAR, "San Diego")

        settings = ApiSettings.from_env()

        assert settings.latitude == 32.7157
        assert settings.longitude == -117.1611
        assert settings.city_name == "San Diego"

    @pytest.mark.parametrize(
        ("variable", "value"),
        [(LATITUDE_ENV_VAR, "north"), (LONGITUDE_ENV_VAR, "west")],
    )
    def test_from_env_invalid_coordinate(
        self, monkeypatch: pytest.MonkeyPatch, variable: str, value: str
    ) -> None:
        """Test that nonnumeric coordinates identify the invalid variable."""
        monkeypatch.setenv(variable, value)

        with pytest.raises(ValueError, match=variable):
            ApiSettings.from_env()

    def test_from_env_missing_vars_lists_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that every missing required variable is named in the error."""
        monkeypatch.delenv("OPENWEATHER_API_KEY")
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        monkeypatch.delenv(SECRET_ENV_VAR)

        with pytest.raises(RuntimeError) as excinfo:
            ApiSettings.from_env()

        message = str(excinfo.value)
        assert "OPENWEATHER_API_KEY" in message
        assert "ANTHROPIC_API_KEY" in message
        assert SECRET_ENV_VAR in message

    def test_from_env_non_integer_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that a non-integer port fails with a clear error."""
        monkeypatch.setenv(API_PORT_ENV_VAR, "eight-thousand")

        with pytest.raises(ValueError, match=API_PORT_ENV_VAR):
            ApiSettings.from_env()

    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_port_out_of_range(self, port: int) -> None:
        """Test that out-of-range ports are rejected at construction."""
        with pytest.raises(ValueError, match="port"):
            _settings(port)


class TestServe:
    """Tests for the serve coroutine."""

    @pytest.mark.asyncio()
    async def test_serve_boots_and_healthz_responds(self) -> None:
        """Test that serve boots a real server and healthz answers."""
        port = _free_port()
        stop = asyncio.Event()
        task = asyncio.create_task(serve(_settings(port), stop=stop))

        try:
            assert await _wait_for_healthz(port) == {"ok": True}
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        assert await _healthz_refused(port)

    @pytest.mark.asyncio()
    async def test_serve_sigterm_triggers_graceful_shutdown(self) -> None:
        """Test that SIGTERM stops the server and cleans up the runner."""
        port = _free_port()
        task = asyncio.create_task(serve(_settings(port)))

        await _wait_for_healthz(port)
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, timeout=5)

        assert await _healthz_refused(port)

    @pytest.mark.asyncio()
    async def test_serve_cleans_up_runner_when_stopped(self) -> None:
        """Test that serve wires services through start_api and cleans up."""
        runner = MagicMock()
        runner.cleanup = AsyncMock()
        stop = asyncio.Event()
        stop.set()
        settings = _settings(8002)

        with patch(
            "weather_friend.api.start_api", new=AsyncMock(return_value=runner)
        ) as start_mock:
            await serve(settings, stop=stop)

        start_mock.assert_awaited_once_with(ANY, ANY, "127.0.0.1", 8002)
        runner.cleanup.assert_awaited_once()


class TestMain:
    """Tests for the synchronous main entrypoint."""

    def test_main_runs_serve_with_env_settings(self) -> None:
        """Test that main loads env settings and runs serve with them."""
        sentinel = _settings(8002)

        with (
            patch("weather_friend.api.load_dotenv") as dotenv_mock,
            patch(
                "weather_friend.api.ApiSettings.from_env", return_value=sentinel
            ) as from_env_mock,
            patch("weather_friend.api.serve", new_callable=MagicMock) as serve_mock,
            patch("weather_friend.api.asyncio.run") as run_mock,
        ):
            main()

        dotenv_mock.assert_called_once()
        from_env_mock.assert_called_once()
        serve_mock.assert_called_once_with(sentinel)
        run_mock.assert_called_once_with(serve_mock.return_value)
