"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from typing import Final

from weather_friend.auth_middleware import SECRET_ENV_VAR

DEFAULT_LATITUDE: Final[float] = 37.3382
DEFAULT_LONGITUDE: Final[float] = -121.8863
DEFAULT_CITY_NAME: Final[str] = "San Jose"

DEFAULT_API_HOST: Final[str] = "127.0.0.1"
DEFAULT_API_PORT: Final[int] = 8002
API_PORT_ENV_VAR: Final[str] = "WEATHER_FRIEND_API_PORT"
LATITUDE_ENV_VAR: Final[str] = "WEATHER_FRIEND_LATITUDE"
LONGITUDE_ENV_VAR: Final[str] = "WEATHER_FRIEND_LONGITUDE"
CITY_NAME_ENV_VAR: Final[str] = "WEATHER_FRIEND_CITY_NAME"


def _float_from_env(name: str, default: float) -> float:
    """Read a floating-point environment variable with a clear error."""
    raw_value = os.environ.get(name, str(default))
    try:
        return float(raw_value)
    except ValueError as exc:
        msg = f"{name} must be a number, got {raw_value!r}"
        raise ValueError(msg) from exc


@dataclass(frozen=True)
class ApiSettings:
    """Settings for the standalone RubotPaul API process.

    The API is the entire service (a ``systemd --user`` unit on the VPS
    bound to localhost), so no Discord credentials are required.

    Attributes:
        openweather_api_key: OpenWeatherMap API key.
        anthropic_api_key: Anthropic Claude API key for narratives.
        host: Interface the API binds to (localhost-only on the VPS).
        port: TCP port the API listens on.
        latitude: Location latitude for weather queries.
        longitude: Location longitude for weather queries.
        city_name: Display name for the forecast city.
    """

    openweather_api_key: str
    anthropic_api_key: str
    host: str = DEFAULT_API_HOST
    port: int = DEFAULT_API_PORT
    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    city_name: str = DEFAULT_CITY_NAME

    def __post_init__(self) -> None:
        """Validate field ranges after initialization.

        Raises:
            ValueError: If port is outside the valid TCP range.
        """
        if not 1 <= self.port <= 65535:
            msg = f"port must be 1-65535, got {self.port}"
            raise ValueError(msg)
        if not -90 <= self.latitude <= 90:
            msg = f"latitude must be -90 to 90, got {self.latitude}"
            raise ValueError(msg)
        if not -180 <= self.longitude <= 180:
            msg = f"longitude must be -180 to 180, got {self.longitude}"
            raise ValueError(msg)
        if not self.city_name.strip():
            msg = "city name must not be blank"
            raise ValueError(msg)

    @classmethod
    def from_env(cls) -> "ApiSettings":
        """Create ApiSettings from environment variables.

        RUBOTPAUL_SHARED_SECRET is validated here (fail fast at boot with
        a clear message) even though the auth middleware reads it from the
        environment on each request.

        Returns:
            An ApiSettings instance populated from the environment.

        Raises:
            RuntimeError: If any required environment variable is unset,
                naming every missing variable.
            ValueError: If WEATHER_FRIEND_API_PORT is not a valid port.
        """
        required = ("OPENWEATHER_API_KEY", "ANTHROPIC_API_KEY", SECRET_ENV_VAR)
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            msg = "missing required environment variables: " + ", ".join(missing)
            raise RuntimeError(msg)
        raw_port = os.environ.get(API_PORT_ENV_VAR, str(DEFAULT_API_PORT))
        try:
            port = int(raw_port)
        except ValueError as exc:
            msg = f"{API_PORT_ENV_VAR} must be an integer, got {raw_port!r}"
            raise ValueError(msg) from exc
        return cls(
            openweather_api_key=os.environ["OPENWEATHER_API_KEY"],
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            port=port,
            latitude=_float_from_env(LATITUDE_ENV_VAR, DEFAULT_LATITUDE),
            longitude=_float_from_env(LONGITUDE_ENV_VAR, DEFAULT_LONGITUDE),
            city_name=os.environ.get(CITY_NAME_ENV_VAR, DEFAULT_CITY_NAME).strip(),
        )
