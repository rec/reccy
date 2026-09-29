from datetime import UTC, datetime, timedelta

import pytest

from reccy.runtime.http_freshness import response_freshness

START = datetime(2026, 1, 1, tzinfo=UTC)


def test_max_age_precedes_expires_and_counts_network_and_resident_age() -> None:
    result = response_freshness(
        {
            'Date': 'Thu, 01 Jan 2026 00:00:00 GMT',
            'Age': '8',
            'Cache-Control': 'max-age=20',
            'Expires': 'Thu, 01 Jan 2026 00:00:01 GMT',
        },
        {},
        START,
        START + timedelta(seconds=2),
        START + timedelta(seconds=10),
    )
    assert result.storable
    assert result.lifetime_seconds == 20
    assert result.age_seconds == 18
    assert result.fresh


@pytest.mark.parametrize(
    ('response_headers', 'request_headers', 'storable'),
    [
        ({}, {}, True),
        ({'Cache-Control': 'no-cache, max-age=60'}, {}, True),
        ({'Cache-Control': 'no-store, max-age=60'}, {}, False),
        ({'Cache-Control': 'max-age=60'}, {'Cache-Control': 'no-cache'}, True),
        ({'Cache-Control': 'max-age=60'}, {'Cache-Control': 'no-store'}, False),
        ({'Cache-Control': 'max-age=60', 'Vary': '*'}, {}, True),
        ({'Cache-Control': 'max-age=garbage'}, {}, True),
        ({'Cache-Control': 'max-age=60'}, {'Cache-Control': 'max-age=bad'}, True),
    ],
)
def test_restricted_responses_are_not_reused_without_validation(
    response_headers: dict[str, str],
    request_headers: dict[str, str],
    storable: bool,
) -> None:
    result = response_freshness(response_headers, request_headers, START, START, START)
    assert result.storable is storable
    assert not result.fresh


def test_expires_and_vary_allow_a_fresh_private_response() -> None:
    result = response_freshness(
        {
            'Expires': 'Thu, 01 Jan 2026 00:01:00 GMT',
            'Vary': 'Accept-Language, Accept-Encoding',
        },
        {},
        START,
        START,
        START + timedelta(seconds=10),
    )
    assert result.fresh
    assert result.vary == ['accept-language', 'accept-encoding']


def test_apparent_age_expires_a_response_with_a_stale_date() -> None:
    result = response_freshness(
        {
            'Date': 'Wed, 31 Dec 2025 23:59:00 GMT',
            'Cache-Control': 'max-age=30',
        },
        {},
        START,
        START,
        START,
    )
    assert result.age_seconds == 60
    assert not result.fresh


def test_request_max_age_limits_reuse() -> None:
    result = response_freshness(
        {'Cache-Control': 'max-age=60'},
        {'Cache-Control': 'max-age=0'},
        START,
        START,
        START + timedelta(seconds=1),
    )
    assert not result.fresh
