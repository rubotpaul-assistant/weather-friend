# weather-friend

**Oracle of the Skies** — a weather service that protects invisible labor by
remembering the weather and deciding what everyone should wear, exposed as a
local HTTP API that **RubotPaul** (the household assistant) calls before the
day begins.

---

## Mission

Every household has someone who silently runs the weather check. They open
the app, glance at the forecast, mentally translate "47°F with wind" into
"long pants, the heavier jacket, and the rain shell just in case," and then
remind everyone else on the way out the door. That work is real, it is
constant, and it is rarely counted.

**weather-friend exists to make that work disappear into a service.**

The Oracle turns raw conditions into specific, gender-neutral clothing
suggestions — layers, footwear, accessories — written in plain language
anyone can follow. RubotPaul fetches the forecast from this API each morning
and posts it to the household Discord channel. No one has to remember. No
one has to translate. No one has to nag. The mental load that used to live
in one person's head now lives in a scheduled task.

This is a small service with a focused goal: redistribute a tiny piece of
invisible labor so the people who carry it can carry less.

> **History:** weather-friend began life as a standalone Discord bot with a
> `/weather` slash command and its own 7am posting loop. Per the RubotPaul
> migration (see the migration kit's `PIVOT.md`), RubotPaul now owns the
> Discord surface and the morning schedule; the bot layer was retired and
> only the API service remains.

---

## How It Works

When RubotPaul (or anything holding the shared secret) calls the API:

1. The service pulls current conditions for the configured location from
   **OpenWeatherMap**.
2. For `/api/v1/weather/forecast`, it sends those conditions to **Claude**
   (`claude-sonnet-4-5`) with a system prompt that instructs it to reply as
   the "Oracle of the Skies" — warm, lightly mystical, and **primarily
   focused on practical, inclusive clothing advice**.
3. It returns structured JSON: the raw weather data, plus (for forecast)
   the Oracle's narrative. RubotPaul renders and posts it to Discord.

The 7:00 AM Pacific morning post now lives in RubotPaul's scheduler, which
calls this API and posts the result itself.

---

## Architecture

The codebase follows a small, layered structure. Each layer depends only on
the one beneath it.

```
┌──────────────────────────────────────────────┐
│  HTTP API (Interface Layer)                  │
│  weather_friend/api.py                       │
│   • GET /api/v1/weather/data                 │
│   • GET /api/v1/weather/forecast             │
│   • GET /healthz (unauthenticated)           │
│   • HMAC bearer auth via auth_middleware.py  │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  Services (Application Layer)                │
│  weather_friend/services/                    │
│   • WeatherService  → OpenWeatherMap (httpx) │
│   • MessageService  → Claude (anthropic SDK) │
└──────────────────┬───────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────┐
│  Models (Domain Layer)                       │
│  weather_friend/models/weather.py            │
│   • WeatherData (frozen dataclass)           │
└──────────────────────────────────────────────┘

         Configuration: weather_friend/config.py
         Entry point:   python -m weather_friend.api
```

### Module map

| Path | Responsibility |
|------|----------------|
| `weather_friend/api.py` | aiohttp app factory (`build_app`), the `/api/v1/weather/*` endpoints, `/healthz`, and the `serve()` entrypoint with graceful SIGINT/SIGTERM shutdown. |
| `weather_friend/auth_middleware.py` | Vendored RubotPaul HMAC bearer-token middleware (`RUBOTPAUL_SHARED_SECRET`). |
| `weather_friend/config.py` | `ApiSettings` frozen dataclass. Fails fast at boot, naming every missing required env var. |
| `weather_friend/services/weather_service.py` | `WeatherService`: async OpenWeatherMap client returning a `WeatherData`. |
| `weather_friend/services/message_service.py` | `MessageService`: builds the Oracle prompt and calls the Anthropic Claude API. |
| `weather_friend/models/weather.py` | `WeatherData` immutable dataclass + `temp_range_summary` formatter. |

---

## Code Choices

A short tour of why the codebase looks the way it does.

- **aiohttp with an app factory.** `build_app(weather_service,
  message_service)` receives its services via constructor injection, which
  keeps the HTTP layer testable without live upstream calls, and mounts
  `/api/v1/*` as a subapp so the HMAC middleware guards exactly the
  authenticated surface (`/healthz` stays open for liveness probes).
- **Constructor injection over module globals.** Services are constructed
  once in `serve()` and passed down. Nothing reaches into a global client.
  Tests substitute fakes for both services.
- **Frozen dataclasses for config and data.** `ApiSettings` and
  `WeatherData` are `@dataclass(frozen=True)`. Configuration can't drift
  mid-run, and weather payloads can't be mutated by accident as they pass
  through layers.
- **`httpx.AsyncClient` for HTTP.** Async-native, supports timeouts (set to
  10s), and raises typed errors (`HTTPStatusError`, `RequestError`) that
  the service catches, logs, and re-raises so callers see real failure
  modes.
- **Anthropic async SDK with a system prompt.** The Oracle's voice and the
  "clothing advice is the main event" instruction live in
  `ORACLE_SYSTEM_PROMPT` as a single editable string. The user prompt is a
  template populated from `WeatherData` fields — no string concatenation
  scattered across the code.
- **Fail-fast env validation.** `ApiSettings.from_env()` raises one
  `RuntimeError` naming every missing required variable, so a misconfigured
  systemd unit fails loudly at boot instead of 401ing or 502ing at 7am.
- **Imperial units at the API boundary.** OpenWeatherMap is queried with
  `units=imperial` so the Oracle's audience gets °F and mph without an
  extra conversion layer.
- **Strict typing, top to bottom.** `mypy` runs in strict mode
  (`disallow_untyped_defs`, `warn_return_any`, etc.) and ruff is configured
  with a broad rule set. Every public function has Google-style docstrings
  with `Args`, `Returns`, `Raises`.
- **Localhost-only by design.** The service binds `127.0.0.1` on the VPS it
  shares with RubotPaul. Nothing is exposed to the network; Tailscale is
  the transport for anything remote.

---

## API Reference

### HTTP interface

All `/api/v1/*` routes require `Authorization: Bearer
<caller_id>.<timestamp>.<hmac_hex>` minted from `RUBOTPAUL_SHARED_SECRET`
(HMAC-SHA256, 300s TTL). `/healthz` is unauthenticated.

| Endpoint | Behavior |
|----------|----------|
| `GET /api/v1/weather/data?location=...` | Serialized `WeatherData` for the location (or the configured default). `400` on unknown location. |
| `GET /api/v1/weather/forecast?location=...` | Same data plus `narrative`: the Claude-generated Oracle forecast with clothing recommendations. |
| `GET /healthz` | `{"ok": true}` liveness probe. |

### Internal Python API

#### `weather_friend.config.ApiSettings`

Frozen dataclass loaded from environment variables.

```python
ApiSettings.from_env() -> ApiSettings
```

Required env vars: `OPENWEATHER_API_KEY`, `ANTHROPIC_API_KEY`,
`RUBOTPAUL_SHARED_SECRET`. Optional: `WEATHER_FRIEND_API_PORT` (default
`8002`); location fields default to San Jose, CA. Raises `RuntimeError`
naming every missing required var, `ValueError` for an invalid port.

#### `weather_friend.api`

```python
build_app(weather_service, message_service) -> web.Application
start_api(weather_service, message_service, host, port) -> web.AppRunner
serve() -> None   # python -m weather_friend.api
```

#### `weather_friend.services.weather_service.WeatherService`

```python
WeatherService(api_key: str, lat: float, lon: float, city: str)

async WeatherService.get_current_weather() -> WeatherData
async WeatherService.get_weather_for_location(location: str) -> WeatherData
```

Calls OpenWeatherMap for current conditions and Open-Meteo for complete
local-day daily highs and lows. Two-part locations are tried unchanged first;
after a 404, possible US city/state inputs are retried with the `US` country
code. The configured default uses exact coordinates. Requests have a 10-second timeout. Raises
`httpx.HTTPStatusError` on non-2xx responses, `httpx.RequestError` on network
failure, and `ValueError` for an unknown location.

#### `weather_friend.services.message_service.MessageService`

```python
MessageService(api_key: str)

async MessageService.generate_forecast_message(weather: WeatherData) -> str
```

Calls Claude (`claude-sonnet-4-5-20250929`) with a 300-token cap, the
Oracle system prompt, and a templated user message containing the
`WeatherData` fields. Raises `anthropic.APIError` on API failure and
`ValueError` if the response is empty.

#### `weather_friend.models.weather.WeatherData`

Immutable dataclass with `city`, `temp_f`, `feels_like_f`, `humidity`,
`description`, `wind_speed_mph`, `high_f`, `low_f`, `icon`, plus the
`temp_range_summary` property (`"54°F – 68°F"`).

### External APIs used

| API | Purpose | Auth |
|-----|---------|------|
| **OpenWeatherMap** Current Weather Data | Source of truth for conditions. | `appid` query parameter. |
| **Anthropic Messages API** | Generates the Oracle's clothing advice. | `ANTHROPIC_API_KEY` via SDK. |

---

## Configuration

Copy `.env.example` to `.env` and fill in:

```bash
OPENWEATHER_API_KEY=...        # https://openweathermap.org/api
ANTHROPIC_API_KEY=...          # https://console.anthropic.com/
RUBOTPAUL_SHARED_SECRET=...    # shared across RubotPaul-callable services
WEATHER_FRIEND_API_PORT=8002   # optional, default 8002
```

Location overrides (defaults shown) live in `ApiSettings`:
`latitude=37.3382`, `longitude=-121.8863`, `city_name="San Jose"`. Adjust
by editing `config.py` or extending `ApiSettings.from_env`.

---

## Installation

```bash
git clone <repository-url>
cd weather-friend

pip install -r requirements-dev.txt
pre-commit install
```

## Running

The HTTP API is the entire service — there is no separate bot process. It
binds to `127.0.0.1` on `WEATHER_FRIEND_API_PORT` (default `8002`):

```bash
python -m weather_friend.api
```

Or via the `Procfile` entry:

```bash
honcho start web
```

RubotPaul calls it locally on the VPS with an HMAC bearer token minted
from the shared secret:

```bash
curl -H "Authorization: Bearer <caller_id>.<timestamp>.<hmac_hex>" \
  "http://127.0.0.1:8002/api/v1/weather/data?location=San+Jose,CA"
curl "http://127.0.0.1:8002/healthz"   # unauthenticated liveness probe
```

Sample `systemd --user` unit (`~/.config/systemd/user/weather-friend.service`;
localhost-only binding, so no Tailscale-IP wiring is needed):

```ini
[Unit]
Description=weather-friend API for RubotPaul
After=network.target

[Service]
WorkingDirectory=%h/weather-friend
EnvironmentFile=%h/weather-friend/.env
ExecStart=%h/weather-friend/.venv/bin/python -m weather_friend.api
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now weather-friend.service
```

---

## Development

### Quality scripts

Always invoke tools through `./scripts/*` instead of running them directly.

```bash
./scripts/check-all.sh     # everything: format, lint, types, tests, security
./scripts/test.sh          # pytest with branch coverage
./scripts/lint.sh          # ruff + mypy
./scripts/format.sh --fix  # black + isort
./scripts/security.sh      # bandit + pip-audit
./scripts/mutation.sh      # mutmut
```

### Quality bar

| Metric | Threshold |
|--------|-----------|
| Branch test coverage | ≥ 90% |
| Mutation score | ≥ 80% |
| Cyclomatic complexity | ≤ 10 per function |
| mypy | strict, no unjustified `# type: ignore` |
| Ruff | clean across `E,W,F,I,N,UP,B,C4,SIM,TCH,RUF` |
| Bandit | no findings |

CI enforces these gates. See `CLAUDE.md` for the full Stay Green workflow.

### Project layout

```
weather-friend/
├── weather_friend/
│   ├── api.py                # HTTP API + service entry point
│   ├── auth_middleware.py    # RubotPaul HMAC bearer auth
│   ├── config.py             # ApiSettings dataclass
│   ├── services/
│   │   ├── weather_service.py   # OpenWeatherMap client
│   │   └── message_service.py   # Claude client + Oracle prompt
│   └── models/weather.py     # WeatherData
├── tests/                    # Unit + integration tests
├── scripts/                  # Quality control scripts
├── .github/workflows/        # CI / security / dependency review
├── pyproject.toml            # Tool config (mypy strict, coverage 90%)
├── .pre-commit-config.yaml   # 32 quality checks
├── Procfile                  # Process definition (honcho/PaaS)
└── runtime.txt               # Python 3.11.11
```

---

## License

MIT License

## Attribution

Generated with [Start Green Stay Green](https://github.com/Geoffe-Ga/start_green_stay_green) — maximum quality Python projects from day one.
