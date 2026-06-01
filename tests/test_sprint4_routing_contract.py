from types import SimpleNamespace

import polyline
import pytest

from app.models import GeoPoint
from app.services.base_service import ExternalAPIError
from app.routing import (
    CanonicalRoute,
    ConsumptionEngineEnergyEstimator,
    EnergyEstimateRequest,
    GoogleDirectionsProvider,
    GoogleRoutesProvider,
    SegmentFeatureBuilder,
)
from app.routing.providers import GOOGLE_ROUTES_FIELD_MASK
from app.route_planning.orchestrator import _align_station_results_to_hotspots
import app.route_selector as route_selector


def _google_route(summary: str = "Test Route") -> dict:
    return {
        "summary": summary,
        "overview_polyline": {"points": "_p~iF~ps|U_ulLnnqC"},
        "legs": [
            {
                "distance": {"value": 12_000},
                "duration": {"value": 600},
                "duration_in_traffic": {"value": 720},
                "start_location": {"lat": 41.0, "lng": 29.0},
                "end_location": {"lat": 41.1, "lng": 29.1},
            },
            {
                "distance": {"value": 8_000},
                "duration": {"value": 480},
                "duration_in_traffic": {"value": 540},
                "start_location": {"lat": 41.1, "lng": 29.1},
                "end_location": {"lat": 41.2, "lng": 29.2},
            },
        ],
    }


def _google_routes_route(description: str = "Routes API Route") -> dict:
    return {
        "description": description,
        "distanceMeters": 20_000,
        "duration": "1260s",
        "staticDuration": "1080s",
        "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC"},
        "legs": [
            {
                "distanceMeters": 12_000,
                "duration": "720s",
                "staticDuration": "600s",
                "startLocation": {"latLng": {"latitude": 41.0, "longitude": 29.0}},
                "endLocation": {"latLng": {"latitude": 41.1, "longitude": 29.1}},
                "steps": [{"navigationInstruction": {"instructions": "O-5 Istanbul-Izmir Otoyolu"}}],
            },
            {
                "distanceMeters": 8_000,
                "duration": "540s",
                "staticDuration": "480s",
                "startLocation": {"latLng": {"latitude": 41.1, "longitude": 29.1}},
                "endLocation": {"latLng": {"latitude": 41.2, "longitude": 29.2}},
            },
        ],
    }


def test_canonical_route_preserves_google_distance_duration_and_polyline():
    canonical = CanonicalRoute.from_google_directions_route(_google_route(), index=0)

    assert canonical.provider == "google_directions"
    assert canonical.provider_route_id == "google_directions:0"
    assert canonical.summary == "Test Route"
    assert canonical.distance_km == pytest.approx(20.0)
    assert canonical.duration_min == pytest.approx(18.0)
    assert canonical.duration_in_traffic_min == pytest.approx(21.0)
    assert canonical.traffic_ratio == pytest.approx(21.0 / 18.0)
    assert canonical.polyline == "_p~iF~ps|U_ulLnnqC"
    assert canonical.polyline_quality == "overview"
    assert len(canonical.legs) == 2
    assert canonical.legs[0].distance_km == pytest.approx(12.0)


def test_canonical_route_prefers_directions_step_polylines_when_available():
    step1 = polyline.encode([(41.0, 29.0), (41.05, 29.05), (41.1, 29.1)])
    step2 = polyline.encode([(41.1, 29.1), (41.15, 29.15), (41.2, 29.2)])
    route = _google_route()
    route["overview_polyline"] = {"points": polyline.encode([(41.0, 29.0), (41.2, 29.2)])}
    route["legs"][0]["steps"] = [{"polyline": {"points": step1}}]
    route["legs"][1]["steps"] = [{"polyline": {"points": step2}}]

    canonical = CanonicalRoute.from_google_directions_route(route, index=0)

    assert canonical.polyline_quality == "step"
    assert polyline.decode(canonical.polyline) == [
        (41.0, 29.0),
        (41.05, 29.05),
        (41.1, 29.1),
        (41.15, 29.15),
        (41.2, 29.2),
    ]


def test_directions_step_polyline_is_valid_route_corridor_geometry():
    from app.services.station_logic.polyline_filter import (
        check_stations_on_polyline_with_relaxed_fallback,
    )

    step1 = polyline.encode([(41.0, 29.0), (41.05, 29.10), (41.0, 29.2)])
    route = _google_route()
    route["overview_polyline"] = {"points": polyline.encode([(41.0, 29.0), (41.0, 29.2)])}
    route["legs"][0]["steps"] = [{"polyline": {"points": step1}}]

    canonical = CanonicalRoute.from_google_directions_route(route, index=0)
    station_near_step_geometry = (41.05, 29.10)

    step_flags, step_threshold = check_stations_on_polyline_with_relaxed_fallback(
        [station_near_step_geometry],
        polyline.decode(canonical.polyline),
    )
    overview_flags, overview_threshold = check_stations_on_polyline_with_relaxed_fallback(
        [station_near_step_geometry],
        polyline.decode(route["overview_polyline"]["points"]),
    )

    assert canonical.polyline_quality == "step"
    assert step_threshold == 1.0
    assert step_flags == [True]
    assert overview_threshold == 10.0
    assert overview_flags == [False]


def test_canonical_route_invalid_step_polyline_falls_back_to_overview_quality():
    route = _google_route()
    route["legs"][0]["steps"] = [{"polyline": {"points": "not-a-valid-polyline@@@"}}]

    canonical = CanonicalRoute.from_google_directions_route(route, index=0)

    assert canonical.polyline == "_p~iF~ps|U_ulLnnqC"
    assert canonical.polyline_quality == "overview"


def test_canonical_route_maps_google_routes_response():
    canonical = CanonicalRoute.from_google_routes_route(_google_routes_route(), index=1)
    legacy = canonical.to_google_directions_route()

    assert canonical.provider == "google_routes"
    assert canonical.provider_route_id == "google_routes:1"
    assert canonical.summary == "Routes API Route"
    assert canonical.distance_km == pytest.approx(20.0)
    assert canonical.duration_min == pytest.approx(18.0)
    assert canonical.duration_in_traffic_min == pytest.approx(21.0)
    assert canonical.polyline == "_p~iF~ps|U_ulLnnqC"
    assert canonical.polyline_quality == "high_quality"
    assert len(canonical.legs) == 2
    assert legacy["legs"][0]["steps"][0]["html_instructions"] == "O-5 Istanbul-Izmir Otoyolu"


@pytest.mark.asyncio
async def test_google_routes_provider_request_body_and_field_mask():
    calls = {}

    class FakeService:
        async def request(self, **kwargs):
            calls.update(kwargs)
            return {"routes": [_google_routes_route()]}

    provider = GoogleRoutesProvider(service=FakeService(), api_key="test-key")

    routes = await provider.get_route_alternatives(
        start=GeoPoint(lat=41.0, lon=29.0),
        end=GeoPoint(lat=41.2, lon=29.2),
        avoidances=["tolls", "ferries"],
        waypoints=[GeoPoint(lat=41.1, lon=29.1)],
    )

    assert routes[0].summary == "Routes API Route"
    assert calls["method"] == "POST"
    assert calls["endpoint"] == "/directions/v2:computeRoutes"
    assert calls["headers"]["X-Goog-Api-Key"] == "test-key"
    assert calls["headers"]["X-Goog-FieldMask"] == GOOGLE_ROUTES_FIELD_MASK
    assert calls["json"]["origin"]["location"]["latLng"]["latitude"] == pytest.approx(41.0)
    assert calls["json"]["destination"]["location"]["latLng"]["longitude"] == pytest.approx(29.2)
    assert calls["json"]["polylineQuality"] == "HIGH_QUALITY"
    assert calls["json"]["polylineEncoding"] == "ENCODED_POLYLINE"
    assert calls["json"]["routeModifiers"]["avoidTolls"] is True
    assert calls["json"]["routeModifiers"]["avoidHighways"] is False
    assert calls["json"]["routeModifiers"]["avoidFerries"] is True
    assert calls["json"]["computeAlternativeRoutes"] is False
    assert len(calls["json"]["intermediates"]) == 1


@pytest.mark.asyncio
async def test_google_directions_provider_returns_canonical_routes():
    calls = {}

    async def fake_get_route_alternatives_cached(**kwargs):
        calls.update(kwargs)
        return {"status": "OK", "routes": [_google_route("A"), _google_route("B")]}

    provider = GoogleDirectionsProvider(
        maps_service=SimpleNamespace(get_route_alternatives_cached=fake_get_route_alternatives_cached)
    )

    routes = await provider.get_route_alternatives(
        start=GeoPoint(lat=41.0, lon=29.0),
        end=GeoPoint(lat=41.2, lon=29.2),
        avoidances=["tolls"],
    )

    assert [route.summary for route in routes] == ["A", "B"]
    assert calls["avoidances"] == ["tolls"]
    assert calls["alternatives"] is True


@pytest.mark.asyncio
async def test_google_directions_provider_maps_waypoint_limit_to_typed_error():
    async def fake_get_route_alternatives_cached(**_kwargs):
        return {
            "status": "MAX_WAYPOINTS_EXCEEDED",
            "error_message": "Too many waypoints in request",
            "routes": [],
        }

    provider = GoogleDirectionsProvider(
        maps_service=SimpleNamespace(get_route_alternatives_cached=fake_get_route_alternatives_cached)
    )

    with pytest.raises(ExternalAPIError) as exc_info:
        await provider.get_route_alternatives(
            start=GeoPoint(lat=41.0, lon=29.0),
            end=GeoPoint(lat=41.2, lon=29.2),
            waypoints=[GeoPoint(lat=41.1, lon=29.1)],
        )

    assert exc_info.value.code == "TOO_MANY_WAYPOINTS"
    assert exc_info.value.status_code == 400


def test_route_selector_analysis_exposes_canonical_route():
    analysis = route_selector._analyze_route(_google_route(), index=0)

    assert analysis["distance_km"] == pytest.approx(20.0)
    assert analysis["polyline"] == analysis["canonical_route"].polyline
    assert analysis["canonical_route"].distance_km == pytest.approx(20.0)
    assert analysis["energy_estimate_source"] == "distance_fallback"


def test_segment_feature_builder_uses_canonical_route_polyline():
    canonical = CanonicalRoute.from_google_directions_route(_google_route(), index=0)

    feature_set = SegmentFeatureBuilder(segment_length_km=10.0).build(
        canonical_route=canonical,
        raw_elevations=[10.0, 30.0],
    )

    assert feature_set.canonical_route is canonical
    assert feature_set.segmenter.total_distance_km > 0
    assert len(feature_set.segments) >= 1
    assert feature_set.segments[0].start_point.lat == pytest.approx(38.5)


def test_energy_estimator_wraps_consumption_engine_with_route_id():
    canonical = CanonicalRoute.from_google_directions_route(_google_route(), index=0)
    feature_set = SegmentFeatureBuilder(segment_length_km=10.0).build(canonical_route=canonical)

    class FakeEngine:
        def estimate(self, **kwargs):
            assert kwargs["segments"] is feature_set.segments
            return [
                SimpleNamespace(consumption_kwh=1.25),
                SimpleNamespace(consumption_kwh=2.75),
            ]

    estimate = ConsumptionEngineEnergyEstimator(engine=FakeEngine()).estimate(
        EnergyEstimateRequest(
            segment_features=feature_set,
            vehicle=SimpleNamespace(),
            passenger_count=2,
        )
    )

    assert estimate.route_id == "google_directions:0"
    assert estimate.total_consumption_kwh == pytest.approx(4.0)
    assert len(estimate.segments) == 2


def test_route_analysis_uses_energy_estimator_for_alternative_consumption():
    class FakeEstimator:
        def estimate(self, request):
            assert request.segment_features.canonical_route.summary == "Test Route"
            return SimpleNamespace(total_consumption_kwh=7.5)

    analysis = route_selector._analyze_route(
        _google_route(),
        index=0,
        vehicle=SimpleNamespace(),
        energy_estimator=FakeEstimator(),
    )

    assert analysis["estimated_consumption_kwh"] == pytest.approx(7.5)
    assert analysis["energy_estimate_source"] == "energy_estimator"


def test_efficient_strategy_prefers_lower_energy_not_shorter_distance():
    fast_short_high_energy = {
        "summary": "short",
        "distance_km": 10.0,
        "duration_in_traffic_min": 10.0,
        "estimated_consumption_kwh": 8.0,
    }
    long_low_energy = {
        "summary": "efficient",
        "distance_km": 20.0,
        "duration_in_traffic_min": 20.0,
        "estimated_consumption_kwh": 5.0,
    }

    selected, reason = route_selector._select_by_strategy(
        [fast_short_high_energy, long_low_energy],
        strategy=route_selector.RouteStrategy.EFFICIENT,
        battery_kwh=60.0,
        threshold_percent=10.0,
    )

    assert selected["summary"] == "efficient"
    assert reason == "efficient_by_consumption"


@pytest.mark.asyncio
async def test_route_selector_falls_back_to_directions_when_routes_primary_fails(monkeypatch):
    class FailingPrimary:
        provider_name = "google_routes"

        async def get_route_alternatives(self, **_kwargs):
            raise ValueError("boom")

    class WorkingFallback:
        provider_name = "google_directions"

        async def get_route_alternatives(self, **_kwargs):
            return [CanonicalRoute.from_google_directions_route(_google_route("fallback"), index=0)]

    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )

    result = await route_selector.find_best_route(
        origin=GeoPoint(lat=41.0, lon=29.0),
        destination=GeoPoint(lat=41.2, lon=29.2),
        vehicle_model_id="test",
        strategy=route_selector.RouteStrategy.FASTEST,
        routing_provider=FailingPrimary(),
        fallback_provider=WorkingFallback(),
        vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
    )

    assert result["selected_route"]["summary"] == "fallback"
    assert result["routing_provider"] == "google_directions"


@pytest.mark.asyncio
async def test_route_selector_does_not_fallback_when_primary_reports_waypoint_limit(monkeypatch):
    class WaypointLimitedPrimary:
        provider_name = "google_routes"

        async def get_route_alternatives(self, **_kwargs):
            raise ExternalAPIError(
                "GoogleRoutes",
                400,
                "MAX_WAYPOINTS_EXCEEDED: intermediates limit exceeded",
            )

    class UnexpectedFallback:
        provider_name = "google_directions"

        async def get_route_alternatives(self, **_kwargs):
            raise AssertionError("fallback should not be called for waypoint limit")

    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )

    with pytest.raises(ExternalAPIError) as exc_info:
        await route_selector.find_best_route(
            origin=GeoPoint(lat=41.0, lon=29.0),
            destination=GeoPoint(lat=41.2, lon=29.2),
            vehicle_model_id="test",
            strategy=route_selector.RouteStrategy.FASTEST,
            routing_provider=WaypointLimitedPrimary(),
            fallback_provider=UnexpectedFallback(),
            waypoints=[GeoPoint(lat=41.1, lon=29.1)],
            vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
        )

    assert exc_info.value.code == "TOO_MANY_WAYPOINTS"


def test_align_station_results_to_hotspots_reorders_by_route_distance():
    hotspots = [
        SimpleNamespace(distance_from_start_km=25.0),
        SimpleNamespace(distance_from_start_km=80.0),
    ]
    near_late = SimpleNamespace(hotspot=SimpleNamespace(distance_from_start_km=82.0), best_station=object())
    near_early = SimpleNamespace(hotspot=SimpleNamespace(distance_from_start_km=22.0), best_station=object())
    extra = SimpleNamespace(hotspot=SimpleNamespace(distance_from_start_km=140.0), best_station=object())

    aligned = _align_station_results_to_hotspots(hotspots, [near_late, extra, near_early])

    assert aligned == [near_early, near_late]


def test_align_station_results_to_hotspots_fails_when_station_result_missing():
    hotspots = [
        SimpleNamespace(distance_from_start_km=25.0),
        SimpleNamespace(distance_from_start_km=80.0),
    ]
    only_early = SimpleNamespace(hotspot=SimpleNamespace(distance_from_start_km=22.0), best_station=object())

    with pytest.raises(ExternalAPIError, match="alignment incomplete") as exc_info:
        _align_station_results_to_hotspots(hotspots, [only_early])
    assert exc_info.value.source == "HotspotAlignment"


def test_align_station_results_to_hotspots_fails_when_match_is_stale():
    hotspots = [
        SimpleNamespace(distance_from_start_km=25.0),
    ]
    stale_result = SimpleNamespace(
        hotspot=SimpleNamespace(distance_from_start_km=250.0),
        best_station=object(),
    )

    with pytest.raises(ExternalAPIError, match="drift exceeds tolerance") as exc_info:
        _align_station_results_to_hotspots(hotspots, [stale_result])
    assert exc_info.value.source == "HotspotAlignment"


@pytest.mark.parametrize("drift_km", [50.0, 80.0])
def test_align_station_results_to_hotspots_fails_for_practical_bad_drift(drift_km):
    hotspots = [
        SimpleNamespace(distance_from_start_km=100.0),
    ]
    stale_result = SimpleNamespace(
        hotspot=SimpleNamespace(distance_from_start_km=100.0 + drift_km),
        best_station=object(),
    )

    with pytest.raises(ExternalAPIError, match="drift exceeds tolerance") as exc_info:
        _align_station_results_to_hotspots(hotspots, [stale_result])
    assert exc_info.value.source == "HotspotAlignment"


@pytest.mark.asyncio
async def test_find_best_route_passes_user_waypoints_to_initial_provider(monkeypatch):
    calls = {}
    user_waypoints = [GeoPoint(lat=40.8438, lon=31.1565)]

    class CapturingProvider:
        provider_name = "capture_provider"

        async def get_route_alternatives(self, **kwargs):
            calls.update(kwargs)
            return [CanonicalRoute.from_google_directions_route(_google_route("with waypoint"), index=0)]

    monkeypatch.setattr(
        route_selector,
        "pricing_service",
        SimpleNamespace(get_average_dc_price=lambda: 1.0),
    )

    result = await route_selector.find_best_route(
        origin=GeoPoint(lat=41.0082, lon=28.9784),
        destination=GeoPoint(lat=39.9208, lon=32.8541),
        vehicle_model_id="test",
        strategy=route_selector.RouteStrategy.FASTEST,
        waypoints=user_waypoints,
        routing_provider=CapturingProvider(),
        vehicle_spec=SimpleNamespace(battery_capacity_kwh=60),
    )

    assert result["selected_route"]["summary"] == "with waypoint"
    assert len(calls["waypoints"]) == 1
    assert calls["waypoints"][0].lat == pytest.approx(40.8438)
    assert calls["waypoints"][0].lon == pytest.approx(31.1565)
    assert calls["start"].lat == pytest.approx(41.0082)
    assert calls["end"].lon == pytest.approx(32.8541)
