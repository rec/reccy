"""Private-cache freshness decisions for complete HTTP GET responses."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from pydantic import BaseModel


class HTTPFreshness(BaseModel, frozen=True):
    """Whether a scoped response may be stored and reused at one instant."""

    storable: bool
    fresh: bool
    lifetime_seconds: float
    age_seconds: float
    vary: list[str]


def response_freshness(
    response_headers: Mapping[str, str],
    request_headers: Mapping[str, str],
    request_time: datetime,
    response_time: datetime,
    now: datetime,
) -> HTTPFreshness:
    """Apply RFC 9111 explicit freshness and corrected-age rules.

    This is a private cache. It deliberately grants no heuristic freshness and
    never grants stale reuse. An invalid lifetime is treated as immediately
    stale, rather than guessing an expiration.
    """
    for instant in (request_time, response_time, now):
        if instant.tzinfo is None:
            raise ValueError('HTTP cache times must be timezone-aware')
    if request_time > response_time or response_time > now:
        raise ValueError('HTTP cache times must be ordered')
    response = {key.casefold(): value for key, value in response_headers.items()}
    request = {key.casefold(): value for key, value in request_headers.items()}
    controls = _directives(response.get('cache-control', ''))
    request_controls = _directives(request.get('cache-control', ''))
    vary = [
        v.strip().casefold() for v in response.get('vary', '').split(',') if v.strip()
    ]
    storable = 'no-store' not in controls and 'no-store' not in request_controls

    lifetime = _delta(controls.get('max-age'))
    if lifetime is None and 'max-age' not in controls:
        if (expires := _http_date(response.get('expires'))) is not None:
            date = _http_date(response.get('date')) or response_time
            lifetime = max(0.0, (expires - date).total_seconds())
    if lifetime is None:
        lifetime = 0.0

    date = _http_date(response.get('date')) or response_time
    apparent_age = max(0.0, (response_time - date).total_seconds())
    delay = (response_time - request_time).total_seconds()
    age_header = response.get('age', '').split(',', 1)[0].strip()
    age_value = _delta(age_header) or 0
    initial_age = max(apparent_age, age_value + delay)
    age = initial_age + (now - response_time).total_seconds()
    request_max_age = _delta(request_controls.get('max-age'))
    fresh = (
        storable
        and '*' not in vary
        and 'no-cache' not in controls
        and 'no-cache' not in request_controls
        and request.get('pragma', '').casefold() != 'no-cache'
        and ('max-age' not in request_controls or request_max_age is not None)
    )
    fresh = fresh and age < lifetime
    if request_max_age is not None:
        fresh = fresh and age <= request_max_age
    return HTTPFreshness(
        storable=storable,
        fresh=fresh,
        lifetime_seconds=lifetime,
        age_seconds=age,
        vary=vary,
    )


def _directives(value: str) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for part in re.split(r',(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', value):
        name, _, argument = part.strip().partition('=')
        if name:
            key = name.casefold()
            if key in result:
                result[key] = None
            else:
                result[key] = argument.strip().strip('"') if '=' in part else None
    return result


def _delta(value: str | None) -> int | None:
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    digits = value.lstrip('0') or '0'
    return 2147483648 if len(digits) > 10 else min(int(digits), 2147483648)


def _http_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
