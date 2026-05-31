import asyncio

import pytest

from app.services.google_service import (
    ExternalAPIError,
    GLOBAL_GOOGLE_STATIONS_CACHE,
    GLOBAL_GOOGLE_STATIONS_CACHE_LOCK,
    GLOBAL_GOOGLE_STATIONS_CACHE_MAX_SIZE,
    GLOBAL_GOOGLE_STATIONS_CACHE_TTL_SECONDS,
    GoogleMapsService,
    GeoPoint,
    _make_global_station_cache_entry,
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


@pytest.mark.asyncio
async def test_get_map_stations_prunes_expired_global_cache_entries(monkeypatch):
    service = GoogleMapsService()
    now = 10_000.0

    async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
        GLOBAL_GOOGLE_STATIONS_CACHE.clear()
        GLOBAL_GOOGLE_STATIONS_CACHE["expired"] = _make_global_station_cache_entry(
            _station("expired", 41.0, 29.0),
            now - GLOBAL_GOOGLE_STATIONS_CACHE_TTL_SECONDS - 1,
        )
        GLOBAL_GOOGLE_STATIONS_CACHE["fresh"] = _make_global_station_cache_entry(
            _station("fresh", 41.0, 29.0),
            now,
        )

    monkeypatch.setattr("app.services.google_service.time.time", lambda: now)
    monkeypatch.setattr(service, "search_ev_charging_stations_new", lambda *args, **kwargs: asyncio.sleep(0))

    results = await service.get_map_stations(41.0, 29.0, radius_km=50, zoom=12)

    assert [station["place_id"] for station in results] == ["fresh"]
    async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
        assert "expired" not in GLOBAL_GOOGLE_STATIONS_CACHE
        GLOBAL_GOOGLE_STATIONS_CACHE.clear()


@pytest.mark.asyncio
async def test_get_map_stations_prunes_global_cache_to_max_size(monkeypatch):
    service = GoogleMapsService()
    now = 20_000.0

    async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
        GLOBAL_GOOGLE_STATIONS_CACHE.clear()
        for index in range(GLOBAL_GOOGLE_STATIONS_CACHE_MAX_SIZE + 2):
            place_id = f"station-{index}"
            GLOBAL_GOOGLE_STATIONS_CACHE[place_id] = _make_global_station_cache_entry(
                _station(place_id, 41.0, 29.0),
                now + index,
            )

    monkeypatch.setattr("app.services.google_service.time.time", lambda: now + GLOBAL_GOOGLE_STATIONS_CACHE_MAX_SIZE + 2)
    monkeypatch.setattr(service, "search_ev_charging_stations_new", lambda *args, **kwargs: asyncio.sleep(0))

    await service.get_map_stations(41.0, 29.0, radius_km=50, zoom=12)

    async with GLOBAL_GOOGLE_STATIONS_CACHE_LOCK:
        assert len(GLOBAL_GOOGLE_STATIONS_CACHE) == GLOBAL_GOOGLE_STATIONS_CACHE_MAX_SIZE
        assert "station-0" not in GLOBAL_GOOGLE_STATIONS_CACHE
        assert "station-1" not in GLOBAL_GOOGLE_STATIONS_CACHE
        assert f"station-{GLOBAL_GOOGLE_STATIONS_CACHE_MAX_SIZE + 1}" in GLOBAL_GOOGLE_STATIONS_CACHE
        GLOBAL_GOOGLE_STATIONS_CACHE.clear()


@pytest.mark.asyncio
async def test_snap_to_roads_chunk_failure_is_all_or_nothing(monkeypatch):
    service = GoogleMapsService()
    calls = {"count": 0}

    async def fake_roads_request(endpoint, params):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"snappedPoints": [{"location": {"latitude": 41.0, "longitude": 29.0}}]}
        raise ExternalAPIError("GoogleRoadsAPI", 503, "chunk failed")

    monkeypatch.setattr(service, "_roads_request", fake_roads_request)

    path = [GeoPoint(lat=41.0 + i * 0.001, lon=29.0) for i in range(101)]

    with pytest.raises(ExternalAPIError, match="chunk failed"):
        await service.snap_to_roads(path)
