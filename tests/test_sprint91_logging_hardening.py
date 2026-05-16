"""Sprint 9.1 logging hardening regression tests."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import GeoPoint, WeatherCondition, WeatherInfo
from app.optimization.decision_logger import (
    DecisionLogger,
    DecisionRecord,
    build_route_decision_record,
)
from app.route_planning.orchestrator import (
    _segment_summary,
    _skipped_station_reasons,
    _station_candidate_metadata,
    _station_candidate_summaries,
)
from app.route_segmenter import RouteSegment
from app.soc_simulator import SegmentWithConsumption
from app.utils.cache_manager import begin_cache_trace, cacheable, clear_cache, end_cache_trace, get_cache_metrics_snapshot


def _local_tmp_dir(name: str) -> Path:
    path = Path("logs_test") / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decision(trip_id: str) -> DecisionRecord:
    return DecisionRecord(
        trip_id=trip_id,
        timestamp_iso=datetime.now(timezone.utc).isoformat(),
        origin_lat=41.0,
        origin_lon=29.0,
        destination_lat=40.0,
        destination_lon=32.0,
        total_distance_km=450.0,
        vehicle_id="test",
        battery_kwh=60.0,
        start_soc=80.0,
        arrival_soc_target=15.0,
        smart_plan_enabled=True,
        optimization_mode="balanced",
        weights={},
        num_stops=1,
        per_stop_target_socs=[70.0],
        per_stop_min_required=[40.0],
        per_stop_dynamic_buffers=[5.0],
        predicted_total_time_min=300.0,
        predicted_charge_time_min=30.0,
        predicted_total_cost_tl=200.0,
        predicted_arrival_soc_margin=4.0,
        predicted_high_soc_minutes=0.0,
        J_score=0.4,
    )


def test_decision_logger_concurrent_writes_keep_valid_jsonl():
    log_dir = _local_tmp_dir("concurrent")
    try:
        logger = DecisionLogger(log_dir=log_dir)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: logger.write(_decision(f"trip_{i:03d}")), range(60)))

        assert all(results)
        lines = (next(log_dir.glob("decisions_*.jsonl"))).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 60
        parsed = [json.loads(line) for line in lines]
        assert {item["trip_id"] for item in parsed} == {f"trip_{i:03d}" for i in range(60)}
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


def test_decision_logger_rollover_and_reader_include_suffix_files(monkeypatch):
    log_dir = _local_tmp_dir("rollover")
    monkeypatch.setenv("LOG_DECISIONS_MAX_BYTES", "1024")
    logger = DecisionLogger(log_dir=log_dir)

    try:
        for index in range(8):
            assert logger.write(_decision(f"roll_{index}"))

        files = sorted(log_dir.glob("decisions_*.jsonl"))
        assert len(files) > 1
        merged = logger.load_decisions_with_outcomes(days_back=1)
        assert {item["trip_id"] for item in merged} == {f"roll_{index}" for index in range(8)}
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


def test_objective_breakdown_redacted_by_default_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("LOG_DECISIONS_FULL_OBJECTIVE", raising=False)

    record = build_route_decision_record(
        trip_id="redacted",
        origin=(41.0, 29.0),
        destination=(40.0, 32.0),
        total_distance_km=450.0,
        route_duration_min=300.0,
        battery_kwh=60.0,
        start_soc=80.0,
        arrival_soc_target=15.0,
        final_soc=18.0,
        vehicle_spec={"slug": "test"},
        smart_plan_enabled=True,
        optimization_mode="balanced",
        decision_reason="pareto_optimal",
        num_stops=1,
        selected_stations=[],
        station_candidates=[],
        skipped_station_reasons=[],
        soc_trajectory=[],
        segment_feature_summary={},
        plan_quality={},
        total_elapsed_ms=10,
        objective_breakdown={"J": 0.42, "T_normalized": 0.3, "is_hard_violation": False},
    )

    assert record.schema_version == "trip_decision_v1_1"
    assert record.objective_breakdown == {"J": 0.42, "is_hard_violation": False, "redacted": True}


def test_outcome_endpoint_soft_success_when_jsonl_write_fails(monkeypatch):
    class FailingLogger:
        def write_outcome(self, _record):
            return False

    from app.optimization import decision_logger as dl

    monkeypatch.setattr(dl, "_default_logger", FailingLogger())
    with TestClient(app) as client:
        response = client.post("/trips/trip_soft_fail/outcome", json={"actual_arrival_soc": 18})

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["success"] is True
    assert payload["persisted"] is False
    assert payload["warning"] == "outcome_log_write_failed"


def test_trip_id_regex_rejects_path_like_ids():
    with TestClient(app) as client:
        response = client.post("/trips/bad.path/outcome", json={"actual_arrival_soc": 18})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cache_trace_records_hit_miss_and_set():
    clear_cache()

    calls = {"count": 0}

    @cacheable(prefix="sprint91_cache_test", ttl_seconds=60)
    async def expensive(value):
        calls["count"] += 1
        return {"value": value}

    token = begin_cache_trace()
    try:
        assert await expensive("x") == {"value": "x"}
        assert await expensive("x") == {"value": "x"}
        snapshot = get_cache_metrics_snapshot()
    finally:
        end_cache_trace(token)

    assert calls["count"] == 1
    assert snapshot["total_misses"] == 1
    assert snapshot["total_hits"] == 1
    assert snapshot["total_sets"] == 1
    assert snapshot["by_prefix"]["sprint91_cache_test"] == {"hits": 1, "misses": 1, "sets": 1}


def test_station_candidates_are_capped_and_skipped_reasons_are_multi_reason(monkeypatch):
    monkeypatch.setenv("LOG_DECISIONS_MAX_STATION_CANDIDATES", "2")
    selected = SimpleNamespace(station_id="a", source_provider="google", source_id="a", power_kw=150, power_known=True, availability_status="available", score=1.0, deviation_km=0.5)
    multi_reason = SimpleNamespace(station_id="b", source_provider="google", source_id="b", power_kw=0, power_known=False, availability_status="unavailable", is_compatible=False, score=0.1, deviation_km=2.0)
    third = SimpleNamespace(station_id="c", source_provider="google", source_id="c", power_kw=90, power_known=True, availability_status="unknown", score=0.5, deviation_km=3.0)
    result = SimpleNamespace(best_station=selected, stations=[selected, multi_reason, third])

    assert len(_station_candidate_summaries([result])) == 2
    assert _station_candidate_metadata([result]) == {
        "total_count": 3,
        "logged_count": 2,
        "max_logged": 2,
        "truncated": True,
    }

    skipped = _skipped_station_reasons([result])
    assert skipped[0]["reason_code"] == "incompatible_connector"
    assert skipped[0]["reason_codes"] == ["incompatible_connector", "unavailable", "low_confidence_power"]


def test_segment_feature_vector_is_capped_and_has_weather_summary(monkeypatch):
    monkeypatch.setenv("LOG_DECISIONS_MAX_SEGMENT_FEATURES", "2")
    segments = [
        SegmentWithConsumption(
            segment=RouteSegment(
                index=i,
                start_point=GeoPoint(lat=40.0, lon=29.0),
                end_point=GeoPoint(lat=40.1, lon=29.1),
                distance_km=10.0,
                cumulative_distance_km=(i + 1) * 10.0,
                elevation_gain_m=10.0 + i,
                elevation_loss_m=2.0,
            ),
            consumption_kwh=2.0 + i,
        )
        for i in range(4)
    ]
    checkpoint_weather = [
        (
            SimpleNamespace(cumulative_km=10.0),
            WeatherInfo(
                temp_c=5.0,
                condition=WeatherCondition.CLEAR,
                wind_speed_mps=3.0,
                wind_direction_deg=0,
                precipitation_prob=0.1,
            ),
        )
    ]

    summary = _segment_summary(
        segments,
        checkpoint_weather=checkpoint_weather,
        traffic_ratio=1.2,
    )

    vector = summary["segment_feature_vector"]
    assert vector["total_count"] == 4
    assert vector["logged_count"] == 2
    assert vector["truncated"] is True
    assert "start_point" not in vector["items"][0]
    assert vector["items"][0]["weather"]["temp_c"] == 5.0
    assert summary["min_temp_c"] == 5.0
    assert summary["p95_temp_c"] == 5.0


@pytest.mark.asyncio
async def test_raw_prefilter_reject_audit_counter_and_samples(monkeypatch):
    from app import station_finder
    from app.station_finder import CorridorSearcher

    monkeypatch.setattr(station_finder.feedback_manager, "is_station_blocked", lambda place_id: place_id == "blocked")
    searcher = CorridorSearcher(
        vehicle_model_id="test",
        vehicle_spec=SimpleNamespace(display_name="Test EV", connector_type="CCS"),
    )
    hotspot = SimpleNamespace(location=GeoPoint(lat=40.0, lon=29.0), route_polyline_coords=[])

    await searcher._filter_and_score_google_stations([
        {"place_id": "blocked", "name": "Blocked", "business_status": "OPERATIONAL"},
        {"place_id": "closed", "name": "Closed", "business_status": "CLOSED_TEMPORARILY"},
        {"place_id": "nogeo", "name": "No Geo", "business_status": "OPERATIONAL"},
        {"place_id": "far", "name": "Far", "business_status": "OPERATIONAL", "geometry": {"location": {"lat": 42.0, "lng": 29.0}}, "connector_count": 1, "max_power_kw": 120},
        {"place_id": "poi", "name": "POI", "business_status": "OPERATIONAL", "geometry": {"location": {"lat": 40.0, "lng": 29.0}}, "connector_count": 0, "max_power_kw": 0, "types": []},
        {"place_id": "ac", "name": "AC", "business_status": "OPERATIONAL", "geometry": {"location": {"lat": 40.0, "lng": 29.0}}, "connector_count": 1, "max_power_kw": 11},
    ], hotspot)

    audit = searcher._last_reject_audit
    assert audit["total_rejected"] == 6
    assert audit["by_reason"]["blocked_by_feedback"]["count"] == 1
    assert audit["by_reason"]["non_operational"]["count"] == 1
    assert audit["by_reason"]["invalid_geometry"]["count"] == 1
    assert audit["by_reason"]["too_far_from_route"]["count"] == 1
    assert audit["by_reason"]["no_ev_charge_data"]["count"] == 1
    assert audit["by_reason"]["below_min_power"]["count"] == 1
    assert "lat" not in audit["by_reason"]["too_far_from_route"]["samples"][0]
