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
from app.models.route_models import DriveLeg
from app.optimization.modes import OptimizationMode
from app.optimization.pareto_solver import ParetoSolver, StationOptimizationInput
from app.route_planning.orchestrator import (
    _combine_final_route_waypoints,
    _resolve_display_polyline,
    _snap_display_polyline_for_roads,
)
from app.route_planning.leg_builder import build_multi_legs
from app.route_planning.response_builder import validate_plan_feasibility
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


def test_multi_leg_builder_keeps_later_drive_leg_polyline_optional():
    station = SimpleNamespace(
        station_id="station-1",
        station_name="Station 1",
        location=GeoPoint(lat=40.8, lon=30.2),
        station_info={"_source": "google", "_place_id": "place-1", "connector_count": 1},
        rating=4.4,
        has_toilet=False,
        has_food=False,
        has_shopping=False,
        has_parking=True,
        is_open_now=True,
        power_kw=120.0,
        power_known=True,
        deviation_km=0.4,
        source_provider="google",
        source_id="place-1",
        is_dc=True,
    )
    hotspot = SimpleNamespace(
        segment_index=1,
        soc_at_point=35.0,
        distance_from_start_km=160.0,
        location=station.location,
        remaining_distance_km=240.0,
        min_required_soc=15.0,
        recommended_charge_to=80.0,
    )
    station_result = SimpleNamespace(
        best_station=station,
        stations=[station],
        weather_forecast=[],
    )

    legs, warnings = build_multi_legs(
        start_point=GeoPoint(lat=41.0, lon=29.0),
        end_point=GeoPoint(lat=39.9, lon=32.8),
        total_distance_km=400.0,
        total_duration_min=240.0,
        segments_with_consumption=_segments(count=4, km_each=100.0, kwh_each=8.0),
        start_soc=80.0,
        final_soc=20.0,
        hotspots=[hotspot],
        station_results=[station_result],
        polyline="_p~iF~ps|U_ulLnnqC",
        battery_capacity_kwh=75.0,
    )

    drive_legs = [leg for leg in legs if leg.type == "drive"]
    assert warnings == []
    assert len(drive_legs) == 2
    assert drive_legs[0].polyline == "_p~iF~ps|U_ulLnnqC"
    assert drive_legs[1].polyline == ""


def test_feasibility_uses_leg_consumption_before_route_average():
    leg = DriveLeg(
        type="drive",
        start_point=GeoPoint(lat=40.0, lon=29.0),
        end_point=GeoPoint(lat=40.5, lon=30.0),
        distance_km=150.5,
        duration_minutes=95.0,
        consumption_kwh=19.92,
        start_soc_percent=95.0,
        end_soc_percent=12.0,
    )

    error = validate_plan_feasibility(
        legs=[leg],
        missing_station_warnings=[],
        battery_capacity_kwh=24.0,
        avg_consumption_wh_km=300.0,
    )

    assert error is None


def test_multi_leg_builder_stops_when_required_hotspot_has_no_station():
    hotspot = SimpleNamespace(
        segment_index=2,
        soc_at_point=12.0,
        distance_from_start_km=542.0,
        location=GeoPoint(lat=40.8, lon=30.2),
        remaining_distance_km=161.0,
        min_required_soc=15.0,
        recommended_charge_to=95.0,
    )
    station_result = SimpleNamespace(
        best_station=None,
        stations=[],
        weather_forecast=[],
    )

    legs, warnings = build_multi_legs(
        start_point=GeoPoint(lat=40.0, lon=29.0),
        end_point=GeoPoint(lat=41.0, lon=31.0),
        total_distance_km=703.0,
        total_duration_min=465.0,
        segments_with_consumption=_segments(count=7, km_each=100.0, kwh_each=8.0),
        start_soc=80.0,
        final_soc=25.5,
        hotspots=[hotspot],
        station_results=[station_result],
        polyline="_p~iF~ps|U_ulLnnqC",
        battery_capacity_kwh=61.4,
    )

    assert legs == []
    assert warnings
    assert "542" in warnings[0]
    assert "uygun DC istasyon bulunamadı" in warnings[0]


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


def test_greedy_fallback_prefers_80_when_min_required_allows_it():
    hotspots = [
        SimpleNamespace(
            segment_index=index,
            soc_at_point=25.0,
            distance_from_start_km=(index + 1) * 60.0,
            location=GeoPoint(lat=40.0, lon=29.0),
            remaining_distance_km=500.0 - ((index + 1) * 60.0),
            min_required_soc=15.0,
            recommended_charge_to=80.0,
        )
        for index in range(6)
    ]

    solution = ParetoSolver(OptimizationMode.BALANCED).solve(
        hotspots=hotspots,
        segments_with_consumption=_segments(count=30, km_each=20.0, kwh_each=1.2),
        battery_kwh=82.0,
        start_soc=80.0,
        arrival_soc_target=15.0,
        total_distance_km=600.0,
    )

    assert solution.fallback_used is True
    assert solution.per_stop_target_soc
    assert max(solution.per_stop_target_soc) <= 80.0


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


def test_final_reroute_keeps_user_waypoints_before_charging_station_waypoints():
    user_waypoints = [
        GeoPoint(lat=40.8438, lon=31.1565),
        GeoPoint(lat=40.7650, lon=30.3940),
    ]
    station_waypoints = [
        GeoPoint(lat=40.9000, lon=31.0000),
        GeoPoint(lat=39.9500, lon=32.7000),
    ]

    combined = _combine_final_route_waypoints(user_waypoints, station_waypoints)

    assert combined == user_waypoints + station_waypoints
    assert combined[0].lat == pytest.approx(40.8438)
    assert combined[1].lon == pytest.approx(30.3940)
    assert combined[2].lat == pytest.approx(40.9000)


@pytest.mark.asyncio
async def test_high_quality_provider_polyline_skips_roads_snap():
    original_polyline = polyline.encode([(41.0000, 29.0000), (41.0100, 29.0100)])

    async def fake_snap_to_roads(points, interpolate):
        raise AssertionError("HIGH_QUALITY route geometry must not be overwritten by Roads snap")

    display_polyline, snap_called = await _resolve_display_polyline(
        original_polyline,
        "high_quality",
        fake_snap_to_roads,
    )

    assert snap_called is False
    assert display_polyline == original_polyline


@pytest.mark.asyncio
async def test_overview_polyline_still_uses_roads_snap_fallback_path():
    original_coords = [(41.0000, 29.0000), (41.0100, 29.0100)]
    snapped_coords = [(41.0005, 29.0005), (41.0105, 29.0105)]
    original_polyline = polyline.encode(original_coords)
    calls = {"count": 0}

    async def fake_snap_to_roads(points, interpolate):
        calls["count"] += 1
        return [
            {"location": {"latitude": lat, "longitude": lon}}
            for lat, lon in snapped_coords
        ]

    display_polyline, snap_called = await _resolve_display_polyline(
        original_polyline,
        "overview",
        fake_snap_to_roads,
    )

    assert snap_called is True
    assert calls["count"] == 1
    assert display_polyline == polyline.encode(snapped_coords)


@pytest.mark.asyncio
async def test_snap_to_roads_success_updates_display_polyline_offline_fixture():
    original_coords = [(41.0000, 29.0000), (41.0100, 29.0100)]
    snapped_coords = [(41.0005, 29.0005), (41.0105, 29.0105)]
    original_polyline = polyline.encode(original_coords)
    calls = {}

    async def fake_snap_to_roads(points, interpolate):
        calls["points"] = points
        calls["interpolate"] = interpolate
        return [
            {"location": {"latitude": lat, "longitude": lon}}
            for lat, lon in snapped_coords
        ]

    display_polyline, snap_called = await _snap_display_polyline_for_roads(
        original_polyline,
        fake_snap_to_roads,
    )

    assert snap_called is True
    assert calls["interpolate"] is True
    assert len(calls["points"]) == len(original_coords)
    assert display_polyline == polyline.encode(snapped_coords)


@pytest.mark.asyncio
async def test_snap_to_roads_empty_response_falls_back_to_canonical_polyline():
    original_polyline = polyline.encode([(41.0000, 29.0000), (41.0100, 29.0100)])

    async def fake_snap_to_roads(points, interpolate):
        return []

    display_polyline, snap_called = await _snap_display_polyline_for_roads(
        original_polyline,
        fake_snap_to_roads,
    )

    assert snap_called is False
    assert display_polyline == original_polyline


@pytest.mark.asyncio
async def test_snap_to_roads_malformed_response_falls_back_to_canonical_polyline():
    original_polyline = polyline.encode([(41.0000, 29.0000), (41.0100, 29.0100)])

    async def fake_snap_to_roads(points, interpolate):
        return [{"placeId": "missing-location"}]

    display_polyline, snap_called = await _snap_display_polyline_for_roads(
        original_polyline,
        fake_snap_to_roads,
    )

    assert snap_called is False
    assert display_polyline == original_polyline


@pytest.mark.asyncio
async def test_snap_to_roads_partial_response_falls_back_to_canonical_polyline():
    original_polyline = polyline.encode([
        (41.0000, 29.0000),
        (41.0100, 29.0100),
        (41.0200, 29.0200),
    ])

    async def fake_snap_to_roads(points, interpolate):
        return [{"location": {"latitude": 41.0005, "longitude": 29.0005}}]

    display_polyline, snap_called = await _snap_display_polyline_for_roads(
        original_polyline,
        fake_snap_to_roads,
    )

    assert snap_called is False
    assert display_polyline == original_polyline


@pytest.mark.asyncio
async def test_snap_to_roads_provider_error_falls_back_to_canonical_polyline():
    from app.services.base_service import ExternalAPIError

    original_polyline = polyline.encode([(41.0000, 29.0000), (41.0100, 29.0100)])

    async def fake_snap_to_roads(points, interpolate):
        raise ExternalAPIError("GoogleRoadsAPI", 503, "chunk failed")

    display_polyline, snap_called = await _snap_display_polyline_for_roads(
        original_polyline,
        fake_snap_to_roads,
    )

    assert snap_called is False
    assert display_polyline == original_polyline


@pytest.mark.asyncio
async def test_snap_to_roads_empty_canonical_polyline_hard_fails():
    from app.services.base_service import ExternalAPIError

    async def fake_snap_to_roads(points, interpolate):
        return []

    with pytest.raises(ExternalAPIError, match="Route polyline is empty"):
        await _snap_display_polyline_for_roads("", fake_snap_to_roads)


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
