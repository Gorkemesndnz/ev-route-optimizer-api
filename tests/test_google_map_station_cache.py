import asyncio

import pytest

from app.services.google_service import (
    GLOBAL_GOOGLE_STATIONS_CACHE,
    GLOBAL_GOOGLE_STATIONS_CACHE_LOCK,
    GoogleMapsService,
)


def _station(place_id: str, lat: float, lon: float, power: float = 150.0) -> dict:
    return {
        "place_id": place_id,
        "name": place_id,
        "geometry": {"location": {"lat": lat, "lng": lon}},
        "max_power_kw": power,
    }


@pytest.mark.asyncio
async def test_get_map_stations_uses_cache_snapshot_under_concurrent_writes(monkeypatch):
    service = GoogleMapsService()

    async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
        GLOBAL_GOOGLE_STATIONS_CACHE.clear()
        GLOBAL_GOOGLE_STATIONS_CACHE["seed"] = _station("seed", 41.0, 29.0)

    async def fake_search(*args, **kwargs):
        async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
            index = len(GLOBAL_GOOGLE_STATIONS_CACHE)
            GLOBAL_GOOGLE_STATIONS_CACHE[f"new-{index}"] = _station(
                f"new-{index}",
                41.0 + index * 0.001,
                29.0 + index * 0.001,
            )
        await asyncio.sleep(0)
        return []

    monkeypatch.setattr(service, "search_ev_charging_stations_new", fake_search)

    results = await asyncio.gather(
        *(service.get_map_stations(41.0, 29.0, radius_km=50, zoom=12) for _ in range(8))
    )

    assert all(isinstance(result, list) for result in results)
    async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
        assert len(GLOBAL_GOOGLE_STATIONS_CACHE) >= 2
        GLOBAL_GOOGLE_STATIONS_CACHE.clear()

