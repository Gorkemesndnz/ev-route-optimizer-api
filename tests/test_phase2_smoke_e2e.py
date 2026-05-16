"""
Faz 2 — Uçtan Uca Smoke Test
=============================

Pareto karar mekanizması + outcome backfill akışını full pipeline'da doğrular:

1. _run_pareto_simulation gerçek girdiyle Pareto solver'ı çalıştırır
2. Decision log JSONL'a yazılır (trip_id ile)
3. TestClient ile POST /trips/{trip_id}/outcome → outcome eklenir
4. GET /trips/recent → JOIN sonucu hem decision hem outcome görür

Mock kullanılan tek yer: dış servisler (Google Maps API). İç akış gerçek kod.

Run: pytest tests/test_phase2_smoke_e2e.py -v -s
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.optimization.decision_logger import DecisionLogger
from app.soc_simulator import SegmentWithConsumption
from app.models import GeoPoint


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def isolated_logger(tmp_path, monkeypatch):
    """DecisionLogger'ı tmp dizinine yönlendir, testler birbirini etkilemesin."""
    from app.optimization import decision_logger as dl
    custom = DecisionLogger(log_dir=tmp_path)
    monkeypatch.setattr(dl, "_default_logger", custom)
    return custom, tmp_path


def _make_segments(n=15, km_each=30, kwh_each=6.0):
    """Hand-crafted segment listesi — Istanbul-Ankara benzeri 450 km rota."""
    class _Seg:
        def __init__(self, idx, dist, kwh, cum):
            self.index = idx
            self.distance_km = dist
            self.elevation_gain_m = 50  # küçük yokuş
            self.elevation_loss_m = 30
            self.cumulative_distance_km = cum
            self.start_point = GeoPoint(lat=41.0 + idx * 0.01, lon=29.0 + idx * 0.01)
            self.end_point = GeoPoint(lat=41.0 + (idx + 1) * 0.01, lon=29.0 + (idx + 1) * 0.01)

    out = []
    cum = 0
    for i in range(n):
        cum += km_each
        seg = _Seg(i, km_each, kwh_each, cum)
        out.append(SegmentWithConsumption(segment=seg, consumption_kwh=kwh_each))
    return out


def _make_request_obj(smart_plan=True, mode="balanced"):
    """RouteRequest mimic — _run_pareto_simulation için sadece kullanılan field'lar."""
    return SimpleNamespace(
        start_location=GeoPoint(lat=41.0082, lon=28.9784),  # Istanbul
        end_location=GeoPoint(lat=39.9334, lon=32.8597),    # Ankara
        vehicle_model_id="test_smoke_vehicle",
        smart_plan_enabled=smart_plan,
        optimization_mode=mode,
    )


# =============================================================================
# FULL PIPELINE SMOKE TEST
# =============================================================================

class TestParetoFullPipeline:

    def test_pareto_path_writes_decision_log(self, isolated_logger):
        """
        SENARYO: Smart plan açık, balanced mod ile Pareto solver çalışır
        ve decision log dosyası tmp dizinine yazılır.
        """
        from app.route_planning.orchestrator import _run_soc_simulation

        custom_logger, tmp_path = isolated_logger
        request = _make_request_obj(smart_plan=True, mode="balanced")
        segments = _make_segments(n=15, km_each=30, kwh_each=6.0)

        target_soc, sim_result = _run_soc_simulation(
            segments_with_consumption=segments,
            route_distance_km=450.0,
            battery_kwh=50.0,
            start_soc=80.0,
            arrival_soc=15.0,
            charge_min_soc=18.0,
            charge_target_soc=80.0,
            user_target_soc_override=None,
            avg_speed_kmh=80.0,
            request=request,
            trip_id="smoke_trip_001",
        )

        # 1) Sim sonucu sağlam mı
        assert sim_result is not None
        assert len(sim_result.hotspots) >= 1, "En az 1 hotspot beklenmiştir"
        # 2) per-stop target'lar Pareto tarafından atanmış mı
        for h in sim_result.hotspots:
            assert h.recommended_charge_to >= 30.0
            assert h.recommended_charge_to <= 95.0

        # 3) Decision log dosyası yazılmış mı
        files = list(tmp_path.glob("decisions_*.jsonl"))
        assert len(files) == 1, "Decision log oluşmamış"
        line = files[0].read_text(encoding="utf-8").strip()
        record = json.loads(line)

        # 4) trip_id propagation çalıştı mı
        assert record["trip_id"] == "smoke_trip_001"
        assert record["schema_version"] == "trip_decision_v1_1"
        assert record["record_scope"] == "pareto_solver"
        assert record["timestamp_hour_bucket"].endswith(":00:00+00:00")
        assert record["smart_plan_enabled"] is True
        assert record["optimization_mode"] == "balanced"
        assert record["weights"]["w_time"] == 0.30  # balanced default

        # 5) Anonymize aktif → koordinatlar yuvarlanmış olmalı
        assert record["origin_lat"] == pytest.approx(41.01, abs=0.01)
        assert record["origin_lon"] == pytest.approx(28.98, abs=0.01)

        # 6) Per-stop kararlar dolu mu
        assert len(record["per_stop_target_socs"]) >= 1
        assert len(record["per_stop_min_required"]) == len(record["per_stop_target_socs"])
        assert len(record["per_stop_dynamic_buffers"]) == len(record["per_stop_target_socs"])

        # 7) J score finite (hard constraint geçilmedi)
        assert record["J_score"] < float("inf")

    def test_outcome_backfill_full_loop(self, isolated_logger):
        """
        SENARYO: Plan oluşturuldu → kullanıcı yolculuk yaptı → POST outcome
        → GET /trips/recent join sonucunda outcome görünür.
        """
        from app.route_planning.orchestrator import _run_soc_simulation

        custom_logger, tmp_path = isolated_logger
        request = _make_request_obj()
        segments = _make_segments(n=15)

        # 1) Pareto plan
        target_soc, _ = _run_soc_simulation(
            segments_with_consumption=segments,
            route_distance_km=450.0,
            battery_kwh=50.0,
            start_soc=80.0,
            arrival_soc=15.0,
            charge_min_soc=18.0,
            charge_target_soc=80.0,
            user_target_soc_override=None,
            avg_speed_kmh=80.0,
            request=request,
            trip_id="smoke_trip_002",
        )

        # 2) TestClient ile outcome POST
        with TestClient(app) as client:
            response = client.post(
                "/trips/smoke_trip_002/outcome",
                json={
                    "actual_arrival_soc": 16.5,
                    "actual_total_time_min": 305.0,
                    "actual_charge_time_min": 42.0,
                    "actual_total_cost_tl": 168.0,
                    "actual_num_charges": 2,
                    "user_satisfaction": 4,
                    "notes": "Plan tutarlı, beklenen %15 yerine %16.5 ile vardık",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["data"]["trip_id"] == "smoke_trip_002"

            # 3) Recent endpoint join sonucu
            recent_resp = client.get("/trips/recent?days_back=1")
            assert recent_resp.status_code == 200
            items = recent_resp.json()["data"]
            our_trip = [r for r in items if r["trip_id"] == "smoke_trip_002"]
            assert len(our_trip) == 1
            assert our_trip[0]["outcome"] is not None
            assert our_trip[0]["outcome"]["actual_arrival_soc"] == 16.5
            assert our_trip[0]["outcome"]["user_satisfaction"] == 4

    def test_smart_plan_disabled_skips_pareto(self, isolated_logger):
        """
        SENARYO: smart_plan_enabled=False → Pareto path bypass edilir,
        decision log oluşmaz.
        """
        from app.route_planning.orchestrator import _run_soc_simulation

        custom_logger, tmp_path = isolated_logger
        request = _make_request_obj(smart_plan=False)
        segments = _make_segments(n=15)

        target_soc, sim_result = _run_soc_simulation(
            segments_with_consumption=segments,
            route_distance_km=450.0,
            battery_kwh=50.0,
            start_soc=80.0,
            arrival_soc=15.0,
            charge_min_soc=18.0,
            charge_target_soc=80.0,
            user_target_soc_override=None,  # ChargePlanOptimizer'a gider
            avg_speed_kmh=80.0,
            request=request,
            trip_id="smoke_trip_003",
        )

        # smart_plan kapalı → grid search çalıştı, Pareto log YAZILMADI
        assert sim_result is not None
        files = list(tmp_path.glob("decisions_*.jsonl"))
        assert len(files) == 0, "smart_plan=False iken decision log yazılmamalı"

    def test_different_modes_produce_different_logs(self, isolated_logger):
        """
        SENARYO: time_priority vs cost_priority aynı rotada farklı
        per-stop target kombinasyonları seçer ve log'a farklı mode yazılır.
        """
        from app.route_planning.orchestrator import _run_soc_simulation

        custom_logger, tmp_path = isolated_logger
        segments = _make_segments(n=15)

        for trip_id, mode in [("trip_time", "time_priority"), ("trip_cost", "cost_priority")]:
            request = _make_request_obj(smart_plan=True, mode=mode)
            _run_soc_simulation(
                segments_with_consumption=segments,
                route_distance_km=450.0,
                battery_kwh=50.0,
                start_soc=80.0,
                arrival_soc=15.0,
                charge_min_soc=18.0,
                charge_target_soc=80.0,
                user_target_soc_override=None,
                avg_speed_kmh=80.0,
                request=request,
                trip_id=trip_id,
            )

        # 2 kayıt yazıldı, mode'lar farklı
        files = list(tmp_path.glob("decisions_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        records = [json.loads(l) for l in lines]
        modes = {r["trip_id"]: r["optimization_mode"] for r in records}
        assert modes["trip_time"] == "time_priority"
        assert modes["trip_cost"] == "cost_priority"

        # Time vs cost mod ağırlıkları farklı olmalı
        weights_time = next(r for r in records if r["trip_id"] == "trip_time")["weights"]
        weights_cost = next(r for r in records if r["trip_id"] == "trip_cost")["weights"]
        assert weights_time["w_time"] > weights_cost["w_time"]
        assert weights_cost["w_cost"] > weights_time["w_cost"]


# =============================================================================
# RESPONSE MODEL CONTRACT — trip_id field çağrılabilir
# =============================================================================

class TestResponseModelHasTripId:
    def test_multistop_response_accepts_trip_id(self):
        from app.models.route_models import MultiStopRouteResponse
        resp = MultiStopRouteResponse(
            status="success",
            total_distance_km=450.0,
            total_duration_minutes=300.0,
            total_co2_savings_kg=10.0,
            legs=[],
            trip_id="abc123def456",
        )
        assert resp.trip_id == "abc123def456"

    def test_multistop_response_trip_id_optional(self):
        """Backward compat: trip_id verilmezse None olur."""
        from app.models.route_models import MultiStopRouteResponse
        resp = MultiStopRouteResponse(
            status="success",
            total_distance_km=0.0,
            total_duration_minutes=0.0,
            total_co2_savings_kg=0.0,
            legs=[],
        )
        assert resp.trip_id is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
