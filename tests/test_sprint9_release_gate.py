"""
Sprint 9 - Release Gate Bridge
==============================

Offline, deterministic release-gate fixtures for the Sprint 0-8.1 contract.
These tests must not call live Google/OpenWeather services.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import polyline
import pytest

import app.route_selector as route_selector
from app.infrastructure.station_catalog import (
    AvailabilityStatus,
    NormalizedConnector,
    NormalizedStation,
    PowerSource,
    parse_google_place,
)
from app.models import GeoPoint, RoadAvoidances, RouteStrategy
from app.optimization.modes import OptimizationMode
from app.optimization.pareto_solver import ParetoSolver, StationOptimizationInput
from app.route_planning.safe_harbor import (
    RescueStation,
    SafeHarborResult,
    calculate_safe_harbor_soc,
)
from app.route_segmenter import RouteSegment
from app.routing import CanonicalRoute
from app.soc_simulator import SOCSimulator, SegmentWithConsumption


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass
class _FakeRoutingProvider:
    routes: list[dict]
    provider_name: str = "fake_routes"
    api_version: str = "test"

    async def get_route_alternatives(self, **_kwargs):
        return [
            CanonicalRoute.from_google_directions_route(route, index)
            for index, route in enumerate(self.routes)
        ]


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


def _segments(count: int, km_each: float, kwh_each: float) -> list[SegmentWithConsumption]:
    out: list[SegmentWithConsumption] = []
    for i in range(count):
        start = GeoPoint(lat=40.0 + i * 0.001, lon=29.0)
        end = GeoPoint(lat=40.0 + (i + 1) * 0.001, lon=29.0)
        out.append(
            SegmentWithConsumption(
                segment=RouteSegment(
                    index=i,
                    start_point=start,
                    end_point=end,
                    distance_km=km_each,
                    cumulative_distance_km=km_each * (i + 1),
                    elevation_gain_m=20.0,
                    elevation_loss_m=10.0,
                ),
                consumption_kwh=kwh_each,
            )
        )
    return out


def test_golden_route_fixture_contract_is_stable():
    baseline = json.loads((FIXTURE_DIR / "baseline_istanbul_ankara.json").read_text(encoding="utf-8"))
    response = json.loads((FIXTURE_DIR / "full_response_istanbul_ankara.json").read_text(encoding="utf-8"))

    assert baseline["_meta"]["api_status"] == "success"
    assert response["status"] == "success"
    assert response["total_distance_km"] == pytest.approx(baseline["total_distance_km"], abs=0.1)
    assert response["charge_stops"] == baseline["charge_stops"]
    assert response["safe_harbor_info"] is None
    assert any(leg["type"] == "charge" for leg in response["legs"])

    charge_legs = [leg for leg in response["legs"] if leg["type"] == "charge"]
    assert charge_legs[0]["station"]["id"] == baseline["charge_profile"][0]["station_id"]
    assert charge_legs[0]["station"]["connectors"][0]["power_kw"] == baseline["charge_profile"][0]["power_kw"]


def test_long_route_1250km_pareto_normalization_does_not_saturate_time_or_cost():
    segments = _segments(count=125, km_each=10.0, kwh_each=1.65)
    hotspots = [
        SimpleNamespace(
            segment_index=index,
            soc_at_point=18.0,
            distance_from_start_km=(index + 1) * 10.0,
            location=GeoPoint(lat=40.0, lon=29.0),
            remaining_distance_km=1250.0 - ((index + 1) * 10.0),
            min_required_soc=15.0,
            recommended_charge_to=80.0,
        )
        for index in (19, 42, 65, 88, 110)
    ]
    station_inputs = [
        StationOptimizationInput(
            stop_index=i,
            station_id=f"s{i}",
            power_kw=180.0,
            power_known=True,
            availability_status="available",
            price_tl_per_kwh=7.0,
        )
        for i in range(len(hotspots))
    ]

    solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=segments,
        battery_kwh=82.0,
        start_soc=90.0,
        arrival_soc_target=15.0,
        avg_speed_kmh=90.0,
        total_distance_km=1250.0,
        station_inputs=station_inputs,
        vehicle_dc_max_kw=180.0,
    )

    assert solution.breakdown.is_hard_violation is False
    assert solution.breakdown.J < float("inf")
    assert 0.0 <= solution.breakdown.T_normalized < 1.0
    assert 0.0 <= solution.breakdown.C_normalized < 1.0
    assert solution.metrics.num_stops == 5


def test_low_initial_soc_creates_origin_hotspot_before_route_is_consumed():
    result = SOCSimulator(
        battery_capacity_kwh=60.0,
        start_soc=8.0,
        target_arrival_soc=15.0,
        charge_min_soc=18.0,
        charge_target_soc=80.0,
    ).simulate(
        _segments(count=12, km_each=10.0, kwh_each=2.0),
        total_distance_km=120.0,
    )

    assert result.hotspots
    first = result.hotspots[0]
    assert first.distance_from_start_km == 0
    assert first.soc_at_point == 8.0
    assert first.recommended_charge_to >= 50.0


@pytest.mark.asyncio
async def test_safe_harbor_required_arrival_uses_rescue_station_constraint(monkeypatch):
    import app.route_planning.safe_harbor as safe_harbor

    async def fake_search(lat, lon, radius_m, max_results):
        if radius_m <= 10_000:
            return []
        return [
            {
                "place_id": "rescue_a",
                "name": "Rescue A",
                "geometry": {"location": {"lat": lat + 0.12, "lng": lon}},
                "max_power_kw": 120.0,
                "connector_count": 2,
                "business_status": "OPERATIONAL",
                "rating": 4.2,
            },
            {
                "place_id": "rescue_b",
                "name": "Rescue B",
                "geometry": {"location": {"lat": lat + 0.18, "lng": lon}},
                "max_power_kw": 90.0,
                "connector_count": 1,
                "business_status": "OPERATIONAL",
                "rating": 4.0,
            },
        ]

    async def fake_ghost_leg(destination, station, **_kwargs):
        station.route_distance_km = round(station.distance_km * 1.3, 1)
        station.return_consumption_kwh = 5.0
        station.return_soc_needed = 10.0
        station.required_arrival_soc = 17.0
        return station

    from app.services.google_service import google_maps

    monkeypatch.setattr(google_maps, "search_ev_charging_stations_new", fake_search, raising=False)
    monkeypatch.setattr(safe_harbor, "_calculate_ghost_leg_for_station", fake_ghost_leg)

    result = await calculate_safe_harbor_soc(
        destination=GeoPoint(lat=39.0, lon=32.0),
        vehicle=SimpleNamespace(),
        battery_capacity_kwh=50.0,
    )

    assert result.is_destination_covered is False
    assert result.dynamic_min_arrival_soc == 17.0
    assert result.selected_station_index == 0
    assert result.rescue_stations[0].is_selected is True


@pytest.mark.asyncio
async def test_bridge_forbidden_fixture_filters_recorded_alternative(monkeypatch):
    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )
    provider = _FakeRoutingProvider([
        _route("YSS bridge route", [(41.205, 29.115), (41.210, 29.120)], 20),
        _route("D100 no bridge", [(40.900, 29.200), (40.910, 29.210)], 60),
    ])

    result = await route_selector.find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=40.8, lon=29.3),
        vehicle_model_id="test",
        strategy=RouteStrategy.FASTEST,
        road_avoidances=RoadAvoidances(avoid_bridges=True),
        routing_provider=provider,
        vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
    )

    assert result["selected_route"]["summary"] == "D100 no bridge"
    assert len(result["route_analyses"]) == 1


def test_unknown_kw_station_remains_candidate_with_conservative_planning_power():
    station = NormalizedStation(
        source_provider="google",
        source_id="unknown_kw",
        place_id="unknown_kw",
        name="Unknown kW",
        lat=40.0,
        lon=30.0,
        address=None,
        connectors=[
            NormalizedConnector(
                plug_type="CCS2",
                power_kw=None,
                power_known=False,
                power_source=PowerSource.UNKNOWN,
                count=1,
            )
        ],
    )
    pareto_input = StationOptimizationInput(
        stop_index=0,
        station_id=station.source_id,
        power_kw=0.0,
        power_known=station.power_known,
        charger_type="DC",
        availability_status=station.availability_status.value,
    )

    assert station.power_known is False
    assert station.planning_power_kw == 90.0
    assert pareto_input.planning_power_kw == 90.0
    assert pareto_input.data_confidence_penalty_min == 13.0


def test_availability_missing_available_and_out_of_service_contract():
    missing = parse_google_place({
        "id": "missing",
        "displayName": {"text": "Missing"},
        "location": {"latitude": 40.0, "longitude": 30.0},
        "evChargeOptions": {
            "connectorAggregation": [
                {"type": "EV_CONNECTOR_TYPE_CCS_COMBO_2", "maxChargeRateKw": 100, "count": 2}
            ]
        },
    })
    available = parse_google_place({
        "id": "available",
        "displayName": {"text": "Available"},
        "location": {"latitude": 40.0, "longitude": 30.0},
        "evChargeOptions": {
            "connectorAggregation": [
                {
                    "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                    "maxChargeRateKw": 150,
                    "count": 2,
                    "availableCount": 1,
                    "outOfServiceCount": 0,
                }
            ]
        },
    })
    out_of_service = parse_google_place({
        "id": "oos",
        "displayName": {"text": "OOS"},
        "location": {"latitude": 40.0, "longitude": 30.0},
        "evChargeOptions": {
            "connectorAggregation": [
                {
                    "type": "EV_CONNECTOR_TYPE_CCS_COMBO_2",
                    "maxChargeRateKw": 150,
                    "count": 2,
                    "availableCount": 0,
                    "outOfServiceCount": 2,
                }
            ]
        },
    })

    assert missing.availability_status == AvailabilityStatus.UNKNOWN
    assert available.availability_status == AvailabilityStatus.AVAILABLE
    assert out_of_service.availability_status == AvailabilityStatus.UNAVAILABLE
