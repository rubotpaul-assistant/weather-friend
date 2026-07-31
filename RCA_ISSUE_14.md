# RCA: San Jose daily high is inaccurate

## Problem statement

On 2026-07-31, the live service returned a 94.51°F `high_f` for a bare
`San Jose` query while Apple Weather forecast 88°F. The more explicit
`San Jose,CA` query returned `400 unknown location`.

Reproduction:

1. Call `GET /api/v1/weather/data?location=San%20Jose,CA` with valid auth.
2. Observe a 400 response from the service and a 404 from OpenWeatherMap.
3. Call the endpoint with `location=San%20Jose`.
4. Observe that `high_f` is populated from the current-conditions response.

## Root cause

`WeatherService.get_weather_for_location` passes two-part US city/state input
directly to OpenWeatherMap. OpenWeatherMap's query grammar is
`city,state,country`; with only two components, `CA` is interpreted as the
country code for Canada rather than the state of California.

Separately, `_build_weather_data` maps `main.temp_max` and `main.temp_min` from
the current-conditions endpoint to `high_f` and `low_f`. OpenWeatherMap defines
those as extrema within the current observation, not the calendar day's
forecast high and low.

## Analysis

The API boundary accepts a human-friendly location string but does not make
its interpretation unambiguous before calling the provider. The data mapping
then gives observation-level fields a daily semantic that the upstream
contract does not provide.

## Impact

Household forecasts can target an unintended city or reject an otherwise
valid US city/state query. Even when the location resolves correctly, the
reported high and low can be materially wrong, undermining clothing and
activity advice.

## Contributing factors

- Tests asserted that `San Jose,CA` was forwarded unchanged.
- Fixtures used current-observation extrema as if they represented a day.
- There was no contract test for the provider's forecast endpoint or local-day
  aggregation.
- The household default endpoint required a location string instead of using
  the configured coordinates.

## Fix strategy

1. Use configured latitude/longitude when the API location is omitted.
2. Canonicalize two-part US city/state inputs to `city,state,US`.
3. Preserve valid two-part city/country queries and retry a possible US-state
   interpretation only after the original query returns 404.
4. Resolve arbitrary locations once, then use the returned coordinates for
   forecast retrieval so current and daily data refer to the same place.
5. Source complete local-calendar-day extrema from Open-Meteo's daily fields;
   do not mix observation-level extrema into that range.

## Prevention

Add regression tests for default-coordinate routing, state/country
canonicalization, coordinate reuse, and timezone-aware daily aggregation.
