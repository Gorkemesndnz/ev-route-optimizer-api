from types import SimpleNamespace

import polyline
import pytest

from app.models import GeoPoint, RoadAvoidances, RouteStrategy
from app.routing import CanonicalRoute
import app.route_selector as route_selector


def _route(summary: str, coords: list[tuple[float, float]], duration_min: int) -> dict:
    return {
        "summary": summary,
        "overview_polyline": {"points": polyline.encode(coords)},
        "legs": [
            {
                "distance": {"value": 10_000},
                "duration": {"value": duration_min * 60},
                "duration_in_traffic": {"value": duration_min * 60},
                "start_location": {"lat": coords[0][0], "lng": coords[0][1]},
                "end_location": {"lat": coords[-1][0], "lng": coords[-1][1]},
                "steps": [{"html_instructions": summary}],
            }
        ],
    }


class _FakeRoutingProvider:
    provider_name = "fake_routes"
    api_version = "test"

    def __init__(self, routes: list[dict]):
        self._routes = routes

    async def get_route_alternatives(self, **_kwargs):
        return [
            CanonicalRoute.from_google_directions_route(route, index)
            for index, route in enumerate(self._routes)
        ]


@pytest.mark.asyncio
async def test_avoid_bridges_filters_generic_forbidden_bridge(monkeypatch):
    routing_provider = _FakeRoutingProvider([
        _route("Kuzey Marmara - YSS", [(41.205, 29.115), (41.210, 29.120)], 20),
        _route("Koprusuz alternatif", [(40.900, 29.200), (40.910, 29.210)], 60),
    ])
    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )

    result = await route_selector.find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=40.8, lon=29.3),
        vehicle_model_id="test",
        strategy=RouteStrategy.FASTEST,
        road_avoidances=RoadAvoidances(avoid_bridges=True),
        routing_provider=routing_provider,
        vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
    )

    assert result["selected_route"]["summary"] == "Koprusuz alternatif"
    assert len(result["route_analyses"]) == 1
    assert route_selector._detect_forbidden_bridges(
        coords=[(41.205, 29.115)],
        avoid_all_bridges=True,
        avoid_osmangazi=False,
        avoid_canakkale=False,
    ) == ["Yavuz Sultan Selim KÃ¶prÃ¼sÃ¼"]


@pytest.mark.asyncio
async def test_avoid_private_highways_filters_bot_highway(monkeypatch):
    routing_provider = _FakeRoutingProvider([
        _route("O-5 Istanbul-Izmir Otoyolu", [(40.700, 29.400), (40.710, 29.410)], 20),
        _route("D100 alternatifi", [(40.800, 29.200), (40.810, 29.210)], 60),
    ])
    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )

    result = await route_selector.find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=40.8, lon=29.3),
        vehicle_model_id="test",
        strategy=RouteStrategy.FASTEST,
        road_avoidances=RoadAvoidances(avoid_private_highways=True),
        routing_provider=routing_provider,
        vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
    )

    assert result["selected_route"]["summary"] == "D100 alternatifi"
    assert len(result["route_analyses"]) == 1
    assert "o-5" in route_selector._detect_private_highway(
        _route("O-5 Istanbul-Izmir Otoyolu", [(40.700, 29.400), (40.710, 29.410)], 20)
    )


@pytest.mark.asyncio
async def test_avoid_bridges_returns_constraint_error_when_all_routes_violate(monkeypatch):
    routing_provider = _FakeRoutingProvider([
        _route("YSS alternatifi", [(41.205, 29.115), (41.210, 29.120)], 20),
        _route("FSM alternatifi", [(41.090, 29.070), (41.095, 29.075)], 30),
    ])
    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )

    with pytest.raises(ValueError, match="NO_ROUTE_WITH_CONSTRAINTS"):
        await route_selector.find_best_route(
            origin=GeoPoint(lat=41.0, lon=29.0),
            destination=GeoPoint(lat=40.8, lon=29.3),
            vehicle_model_id="test",
            strategy=RouteStrategy.FASTEST,
            road_avoidances=RoadAvoidances(avoid_bridges=True),
            routing_provider=routing_provider,
            vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
        )
