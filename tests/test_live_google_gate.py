import os

import pytest

from app.models import GeoPoint
from app.services.google_service import google_maps


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_GOOGLE_GATE") != "1",
    reason="Live Google gate is opt-in; set RUN_LIVE_GOOGLE_GATE=1",
)


@pytest.mark.asyncio
async def test_live_google_places_roads_and_elevation_gate():
    if not google_maps.api_key:
        pytest.skip("GOOGLE_API_KEY is not configured")

    places = await google_maps.search_ev_charging_stations_new(
        lat=41.0082,
        lon=28.9784,
        radius_m=5000,
        max_results=1,
    )
    assert isinstance(places, list)

    path = [
        GeoPoint(lat=41.0082, lon=28.9784),
        GeoPoint(lat=41.0150, lon=28.9790),
    ]
    snapped = await google_maps.snap_to_roads(path, interpolate=True)
    assert isinstance(snapped, list)
    assert len(snapped) >= len(path)

    # Encoded polyline for a small known path, enough to verify Elevation auth/API.
    elevation = await google_maps.get_elevation_stats("_p~iF~ps|U_ulLnnqC", samples=2)
    assert set(elevation.keys()) >= {"gain_m", "loss_m", "raw_elevations"}
