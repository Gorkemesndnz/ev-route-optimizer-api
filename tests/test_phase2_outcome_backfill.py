"""
Faz 2 — Outcome Backfill Testleri
==================================

Trip outcome backfill mekanizması için unit + integration testleri.
- DecisionLogger.write_outcome / load_decisions_with_outcomes
- POST /trips/{trip_id}/outcome endpoint
- DecisionRecord ve OutcomeRecord trip_id ile JOIN

Run: pytest tests/test_phase2_outcome_backfill.py -v
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.optimization.decision_logger import (
    DecisionLogger,
    DecisionRecord,
    OutcomeRecord,
    build_route_decision_record,
)


# =============================================================================
# 1. UNIT TESTS — DecisionLogger.write_outcome / join
# =============================================================================

def _build_dummy_decision(trip_id: str = "trip_abc") -> DecisionRecord:
    return DecisionRecord(
        trip_id=trip_id,
        timestamp_iso="2026-04-25T10:00:00+00:00",
        origin_lat=41.01, origin_lon=29.02,
        destination_lat=39.93, destination_lon=32.86,
        total_distance_km=450.0,
        vehicle_id="test_vehicle",
        battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
        smart_plan_enabled=True, optimization_mode="balanced",
        weights={"w_time": 0.30, "w_cost": 0.25, "w_battery": 0.25, "w_safety": 0.20},
        num_stops=2,
        per_stop_target_socs=[55.0, 70.0],
        per_stop_min_required=[50.0, 65.0],
        per_stop_dynamic_buffers=[5.0, 7.5],
        predicted_total_time_min=300.0,
        predicted_charge_time_min=45.0,
        predicted_total_cost_tl=180.0,
        predicted_arrival_soc_margin=3.0,
        predicted_high_soc_minutes=10.0,
        J_score=0.45,
    )


class TestOutcomeWrite:
    def test_write_outcome_creates_file(self, tmp_path):
        log = DecisionLogger(log_dir=tmp_path)
        outcome = OutcomeRecord(
            trip_id="trip_xyz",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            actual_arrival_soc=18.0,
            actual_total_time_min=310.0,
            actual_charge_time_min=50.0,
            actual_total_cost_tl=190.0,
            actual_num_charges=2,
            user_satisfaction=4,
        )
        ok = log.write_outcome(outcome)
        assert ok is True

        files = list(tmp_path.glob("outcomes_*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["trip_id"] == "trip_xyz"
        assert parsed["schema_version"] == "trip_outcome_v1"
        assert parsed["timestamp_hour_bucket"].endswith(":00:00+00:00")
        assert parsed["actual_arrival_soc"] == 18.0
        assert parsed["user_satisfaction"] == 4

    def test_write_outcome_appends_multiple(self, tmp_path):
        log = DecisionLogger(log_dir=tmp_path)
        for i in range(3):
            log.write_outcome(OutcomeRecord(
                trip_id=f"trip_{i}",
                timestamp_iso=datetime.now(timezone.utc).isoformat(),
                actual_arrival_soc=15.0 + i,
            ))
        files = list(tmp_path.glob("outcomes_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3


class TestDecisionOutcomeJoin:
    def test_join_returns_merged_records(self, tmp_path):
        log = DecisionLogger(log_dir=tmp_path)
        # 1 decision + matching outcome
        decision = _build_dummy_decision(trip_id="t1")
        log.write(decision)
        log.write_outcome(OutcomeRecord(
            trip_id="t1",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            actual_arrival_soc=17.0,
            actual_total_time_min=290.0,
        ))

        merged = log.load_decisions_with_outcomes(days_back=1)
        assert len(merged) == 1
        rec = merged[0]
        assert rec["trip_id"] == "t1"
        assert rec["schema_version"] == "trip_decision_v1_1"
        assert rec["outcome"] is not None
        assert rec["outcome"]["actual_arrival_soc"] == 17.0

    def test_decision_without_outcome_has_none(self, tmp_path):
        log = DecisionLogger(log_dir=tmp_path)
        log.write(_build_dummy_decision(trip_id="t_no_outcome"))

        merged = log.load_decisions_with_outcomes(days_back=1)
        assert len(merged) == 1
        assert merged[0]["outcome"] is None

    def test_latest_outcome_wins(self, tmp_path):
        """Aynı trip_id için 2 outcome kaydı varsa en son kazanır."""
        log = DecisionLogger(log_dir=tmp_path)
        log.write(_build_dummy_decision(trip_id="t_dup"))
        log.write_outcome(OutcomeRecord(
            trip_id="t_dup",
            timestamp_iso="2026-04-25T10:00:00+00:00",
            actual_arrival_soc=10.0,
        ))
        log.write_outcome(OutcomeRecord(
            trip_id="t_dup",
            timestamp_iso="2026-04-25T11:00:00+00:00",
            actual_arrival_soc=20.0,  # düzeltilmiş değer
        ))

        merged = log.load_decisions_with_outcomes(days_back=1)
        assert len(merged) == 1
        assert merged[0]["outcome"]["actual_arrival_soc"] == 20.0

    def test_orphan_outcome_no_decision_excluded(self, tmp_path):
        """Sadece outcome var, decision yok → join sonucunda yer almamalı."""
        log = DecisionLogger(log_dir=tmp_path)
        log.write_outcome(OutcomeRecord(
            trip_id="orphan",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            actual_arrival_soc=15.0,
        ))
        merged = log.load_decisions_with_outcomes(days_back=1)
        assert merged == []


class TestRouteFinalDecisionRecord:
    def test_route_final_record_keeps_sprint_8_1_observability_fields(self):
        record = build_route_decision_record(
            trip_id="route_final_001",
            origin=(41.0082, 28.9784),
            destination=(39.9334, 32.8597),
            total_distance_km=452.4,
            route_duration_min=301.2,
            battery_kwh=60.0,
            start_soc=78.0,
            arrival_soc_target=15.0,
            final_soc=18.4,
            vehicle_spec={"slug": "test_ev", "battery_kwh": 60.0, "email": "drop@example.com"},
            smart_plan_enabled=True,
            optimization_mode="balanced",
            decision_reason="pareto_optimal",
            num_stops=1,
            selected_stations=[{
                "stop_index": 0,
                "station_id": "selected",
                "power_kw": 180.0,
                "power_known": True,
                "availability_status": "available",
                "charge_time_min": 24.0,
                "estimated_cost": 210.0,
            }],
            station_candidates=[{"stop_index": 0, "station_id": "selected", "selected": True}],
            skipped_station_reasons=[{
                "stop_index": 0,
                "station_id": "candidate_2",
                "reason_code": "lower_score_than_selected",
            }],
            soc_trajectory=[
                {"stop_index": 0, "arrival_soc": 14.2, "departure_soc": 70.0},
                {"type": "destination", "arrival_soc": 18.4},
            ],
            segment_feature_summary={
                "count": 46,
                "avg_temp_c": 12.5,
                "elevation_gain_m": 680.0,
                "traffic_ratio": 1.18,
            },
            plan_quality={
                "fallback_used": False,
                "warnings": [],
                "provider_versions": {"google_routes": "routes_v2_computeRoutes"},
                "call_counts": {"google_routes": 1, "google_places_new": 1},
            },
            total_elapsed_ms=1234,
            objective_breakdown={"J": 0.42, "T_normalized": 0.3},
        )

        assert record.record_scope == "route_final"
        assert record.station_candidates[0]["station_id"] == "selected"
        assert record.skipped_station_reasons[0]["reason_code"] == "lower_score_than_selected"
        assert record.call_counts["google_routes"] == 1
        assert record.provider_metadata["google_routes"] == "routes_v2_computeRoutes"
        assert record.objective_breakdown["J"] == 0.42
        assert record.segment_feature_summary["avg_temp_c"] == 12.5
        assert "email" not in record.vehicle_spec

    def test_station_candidate_helpers_mark_selected_and_skipped(self):
        from app.route_planning.orchestrator import (
            _skipped_station_reasons,
            _station_candidate_summaries,
        )

        selected = SimpleNamespace(
            station_id="station_a",
            source_provider="google",
            source_id="place_a",
            power_kw=180.0,
            power_known=True,
            availability_status="available",
            score=0.91,
            deviation_km=1.2,
        )
        low_confidence = SimpleNamespace(
            station_id="station_b",
            source_provider="google",
            source_id="place_b",
            power_kw=90.0,
            power_known=False,
            availability_status="unknown",
            score=0.72,
            deviation_km=2.4,
        )
        result = SimpleNamespace(best_station=selected, stations=[selected, low_confidence])

        candidates = _station_candidate_summaries([result])
        skipped = _skipped_station_reasons([result])

        assert candidates[0]["selected"] is True
        assert candidates[1]["selected"] is False
        assert skipped[0]["stop_index"] == 0
        assert skipped[0]["station_id"] == "station_b"
        assert skipped[0]["source_provider"] == "google"
        assert skipped[0]["reason_code"] == "low_confidence_power"
        assert skipped[0]["reason_codes"] == ["low_confidence_power", "low_confidence_availability"]
        assert skipped[0]["score"] == 0.72
        assert skipped[0]["selected_station_id"] == "station_a"


# =============================================================================
# 2. ENDPOINT TESTS — POST /trips/{trip_id}/outcome
# =============================================================================

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestOutcomeEndpoint:
    def test_post_outcome_success(self, client, tmp_path, monkeypatch):
        # Logger'ı tmp_path'e yönlendir
        from app.optimization import decision_logger as dl
        custom = DecisionLogger(log_dir=tmp_path)
        monkeypatch.setattr(dl, "_default_logger", custom)

        payload = {
            "actual_arrival_soc": 18.5,
            "actual_total_time_min": 310,
            "actual_charge_time_min": 48,
            "actual_total_cost_tl": 175.5,
            "actual_num_charges": 2,
            "user_satisfaction": 5,
            "notes": "İyi planlama, beklenen süre tutarlı",
        }
        response = client.post("/trips/test_trip_e2e/outcome", json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        # ApiResponse wrapper
        assert data["success"] is True
        assert data["data"]["trip_id"] == "test_trip_e2e"

        # Dosya yazıldı mı?
        files = list(tmp_path.glob("outcomes_*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["trip_id"] == "test_trip_e2e"
        assert parsed["actual_arrival_soc"] == 18.5
        assert parsed["user_satisfaction"] == 5

    def test_post_outcome_partial_fields(self, client, tmp_path, monkeypatch):
        """Tüm alanlar opsiyonel — sadece arrival_soc gönderilse de kabul etmeli."""
        from app.optimization import decision_logger as dl
        custom = DecisionLogger(log_dir=tmp_path)
        monkeypatch.setattr(dl, "_default_logger", custom)

        payload = {"actual_arrival_soc": 12.0}
        response = client.post("/trips/partial_trip/outcome", json=payload)
        assert response.status_code == 200, response.text
        files = list(tmp_path.glob("outcomes_*.jsonl"))
        assert len(files) == 1

    def test_post_outcome_invalid_satisfaction(self, client):
        """user_satisfaction 1-5 dışı → 422."""
        payload = {"user_satisfaction": 7}
        response = client.post("/trips/x/outcome", json=payload)
        assert response.status_code == 422

    def test_post_outcome_invalid_soc_range(self, client):
        """actual_arrival_soc 0-100 dışı → 422."""
        payload = {"actual_arrival_soc": 150.0}
        response = client.post("/trips/x/outcome", json=payload)
        assert response.status_code == 422

    def test_get_recent_trips(self, client, tmp_path, monkeypatch):
        """Recent trips endpoint join görüyor mu?"""
        from app.optimization import decision_logger as dl
        custom = DecisionLogger(log_dir=tmp_path)
        monkeypatch.setattr(dl, "_default_logger", custom)
        # 1 decision + matching outcome
        custom.write(_build_dummy_decision(trip_id="trip_recent"))
        custom.write_outcome(OutcomeRecord(
            trip_id="trip_recent",
            timestamp_iso=datetime.now(timezone.utc).isoformat(),
            actual_arrival_soc=14.0,
        ))

        response = client.get("/trips/recent?days_back=1")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        items = data["data"]
        # En az 1 kayıt olmalı (test_recent kayıt)
        recent = [r for r in items if r["trip_id"] == "trip_recent"]
        assert len(recent) == 1
        assert recent[0]["outcome"]["actual_arrival_soc"] == 14.0

    def test_get_recent_trips_hidden_in_production(self, client, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        response = client.get("/trips/recent?days_back=1")
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
