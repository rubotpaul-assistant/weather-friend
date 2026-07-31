"""HTTP API exposing raw weather data for RubotPaul.

RubotPaul (the household assistant) calls these endpoints and narrates the
results itself; ``/api/v1/weather/data`` returns raw numbers only, while
``/api/v1/weather/forecast`` additionally includes this service's own
Oracle-of-the-Skies narrative. All ``/api/v1`` routes require the shared
HMAC bearer token; ``/healthz`` is unauthenticated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import TYPE_CHECKING, Any

from aiohttp import web
from dotenv import load_dotenv

from weather_friend.auth_middleware import aiohttp_auth_middleware
from weather_friend.config import ApiSettings
from weather_friend.services.message_service import MessageService
from weather_friend.services.weather_service import WeatherService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from weather_friend.models.weather import WeatherData

    Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

logger = logging.getLogger(__name__)

WEATHER_SERVICE_KEY: web.AppKey[WeatherService] = web.AppKey(
    "weather_service", WeatherService
)
MESSAGE_SERVICE_KEY: web.AppKey[MessageService] = web.AppKey(
    "message_service", MessageService
)


@web.middleware
async def _require_bearer_auth(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """Enforce the vendored HMAC bearer auth on every subapp request.

    Adapts the vendored old-style middleware factory to aiohttp's
    new-style middleware signature so the app avoids the deprecated
    old-style code path.

    Args:
        request: The incoming HTTP request.
        handler: The downstream request handler.

    Returns:
        A 401 JSON error response on auth failure, otherwise the
        wrapped handler's response.
    """
    guarded = await aiohttp_auth_middleware(None, handler)
    return await guarded(request)


def _bad_request(message: str) -> web.HTTPBadRequest:
    """Build a 400 response carrying a JSON error body.

    Args:
        message: Human-readable error description.

    Returns:
        An HTTPBadRequest ready to be raised from a handler.
    """
    return web.HTTPBadRequest(
        text=json.dumps({"error": message}),
        content_type="application/json",
    )


async def _weather_for_request(request: web.Request) -> tuple[WeatherData, str]:
    """Resolve the request's location query into weather data.

    Args:
        request: The incoming HTTP request.

    Returns:
        A tuple of the fetched WeatherData and the requested location.

    Raises:
        web.HTTPBadRequest: If the weather service rejects the location.
    """
    location = request.query.get("location")
    if not location:
        service = request.app[WEATHER_SERVICE_KEY]
        return await service.get_current_weather(), service.city
    try:
        weather = await request.app[WEATHER_SERVICE_KEY].get_weather_for_location(
            location
        )
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    return weather, location


def _serialize_weather(weather: WeatherData, location: str) -> dict[str, Any]:
    """Map a WeatherData instance onto the API's JSON contract.

    Args:
        weather: The weather data to serialize.
        location: The location string the caller asked for.

    Returns:
        A JSON-serializable payload of raw weather fields.
    """
    return {
        "location": location,
        "city": weather.city,
        "temp_f": weather.temp_f,
        "feels_like_f": weather.feels_like_f,
        "high_f": weather.high_f,
        "low_f": weather.low_f,
        "humidity": weather.humidity,
        "wind_speed_mph": weather.wind_speed_mph,
        "description": weather.description,
        "icon": weather.icon,
    }


async def get_weather_data(request: web.Request) -> web.Response:
    """Serve GET /api/v1/weather/data: raw weather for a location.

    Args:
        request: The incoming HTTP request with a ``location`` query param.

    Returns:
        A JSON response with the serialized weather data.
    """
    weather, location = await _weather_for_request(request)
    return web.json_response(_serialize_weather(weather, location))


async def get_weather_forecast(request: web.Request) -> web.Response:
    """Serve GET /api/v1/weather/forecast: raw weather plus narrative.

    Args:
        request: The incoming HTTP request with a ``location`` query param.

    Returns:
        A JSON response with the serialized weather data and the
        generated forecast narrative.
    """
    weather, location = await _weather_for_request(request)
    narrative = await request.app[MESSAGE_SERVICE_KEY].generate_forecast_message(
        weather
    )
    payload = _serialize_weather(weather, location)
    payload["narrative"] = narrative
    return web.json_response(payload)


async def healthz(request: web.Request) -> web.Response:
    """Serve GET /healthz: unauthenticated liveness probe.

    Args:
        request: The incoming HTTP request (unused).

    Returns:
        A JSON response confirming the service is up.
    """
    return web.json_response({"ok": True})


def build_app(
    weather_service: WeatherService, message_service: MessageService
) -> web.Application:
    """Assemble the aiohttp application.

    Args:
        weather_service: Service used to fetch raw weather data.
        message_service: Service used to generate forecast narratives.

    Returns:
        An application with authenticated ``/api/v1`` routes and an
        unauthenticated ``/healthz``.
    """
    api = web.Application(middlewares=[_require_bearer_auth])
    api[WEATHER_SERVICE_KEY] = weather_service
    api[MESSAGE_SERVICE_KEY] = message_service
    api.router.add_get("/weather/data", get_weather_data)
    api.router.add_get("/weather/forecast", get_weather_forecast)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.add_subapp("/api/v1", api)
    return app


async def start_api(
    weather_service: WeatherService,
    message_service: MessageService,
    host: str,
    port: int,
) -> web.AppRunner:
    """Start the API server; the caller owns cleanup.

    Args:
        weather_service: Service used to fetch raw weather data.
        message_service: Service used to generate forecast narratives.
        host: Interface to bind, e.g. the Tailscale IP.
        port: TCP port to listen on.

    Returns:
        The started AppRunner; call ``await runner.cleanup()`` to stop.
    """
    runner = web.AppRunner(build_app(weather_service, message_service))
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("weather-friend API listening on %s:%s", host, port)
    return runner


async def serve(settings: ApiSettings, *, stop: asyncio.Event | None = None) -> None:
    """Run the standalone API until stopped, then clean up the runner.

    Builds the weather and message services from the given settings,
    serves the API, and waits. SIGINT/SIGTERM set the stop event so a
    ``systemctl --user stop`` shuts the server down gracefully.

    Args:
        settings: Environment-derived configuration for the API process.
        stop: Optional externally-controlled shutdown event; a fresh
            event driven only by signals is used when omitted.
    """
    stop_event = stop if stop is not None else asyncio.Event()
    weather_service = WeatherService(
        api_key=settings.openweather_api_key,
        lat=settings.latitude,
        lon=settings.longitude,
        city=settings.city_name,
    )
    message_service = MessageService(api_key=settings.anthropic_api_key)
    runner = await start_api(
        weather_service, message_service, settings.host, settings.port
    )
    loop = asyncio.get_running_loop()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for sig in handled_signals:
        loop.add_signal_handler(sig, stop_event.set)
    try:
        await stop_event.wait()
    finally:
        for sig in handled_signals:
            loop.remove_signal_handler(sig)
        await runner.cleanup()
        logger.info("weather-friend API shut down cleanly")


def main() -> None:
    """Run the standalone weather-friend API (``python -m weather_friend.api``).

    This is the entire service after the RubotPaul cutover: it loads
    ``.env``, validates configuration, and serves until signalled.
    """
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(serve(ApiSettings.from_env()))


if __name__ == "__main__":
    main()
