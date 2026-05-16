"""
Sprint 9 live release gate.

This file is intentionally skipped by default. It may call live Google and
OpenWeather services only when RUN_RELEASE_LIVE_GATE=1 is set.
"""

from __future__ import annotations

import os

import pytest

from app.models import GeoPoint, RouteRequest
from app.route_planning.orchestrator import plan_route


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RELEASE_LIVE_GATE") != "1",
    reason="release-only live API gate; set RUN_RELEASE_LIVE_GATE=1 to run",
)


@pytest.mark.asyncio
async def test_live_release_gate_short_route_succeeds():
    response = await plan_route(
        RouteRequest(
            start_location=GeoPoint(lat=41.0082, lon=28.9784),
            end_location=GeoPoint(lat=40.7654, lon=29.9408),
            vehicle_model_id="mg_electric_51kwh_2022",
            current_soc_percent=85.0,
        )
    )

    assert response.status == "success"
    assert response.total_distance_km > 0
    assert response.trip_id
    assert response.plan_quality is not None
