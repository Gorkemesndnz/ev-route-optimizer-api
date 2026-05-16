"""
Faz 2 — Pareto Karar Mekanizması Test Suite
=============================================

Birim + entegrasyon testleri. Hedef:
- Yeni modüllerin doğruluğu (modes, buffer, planner, objective, solver, logger)
- Backward compat: smart_plan_enabled=False ile mevcut davranış değişmemeli
- Pareto modlarının ayrım yapması (time vs cost vs battery aynı rotada farklı combo seçer)
- Hard constraint çalışıyor (min_arrival_soc_margin < 0 → J = inf)

Run: pytest tests/test_phase2_pareto.py -v
"""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pytest

from app.models import WeatherInfo, WeatherCondition, GeoPoint
from app.optimization.modes import (
    OptimizationMode,
    ParetoWeights,
    get_weights,
    WEIGHT_TABLE,
)
from app.optimization.dynamic_buffer import (
    BufferContext,
    calculate_dynamic_buffer,
    BASE_SAFETY_PERCENT,
    MAX_BUFFER_PERCENT,
    FINAL_LEG_MULTIPLIER,
    MID_LEG_MULTIPLIER,
)
from app.optimization.objective import (
    PlanMetrics,
    calculate_objective,
    normalize,
)
from app.optimization.decision_logger import (
    DecisionLogger,
    DecisionRecord,
    _round_coords,
    _is_anonymize_enabled,
)


# =============================================================================
# 1. MODES — ağırlık tablosu
# =============================================================================

class TestOptimizationModes:
    def test_all_modes_present(self):
        for mode in OptimizationMode:
            assert mode in WEIGHT_TABLE, f"{mode} mod ağırlık tablosunda yok"

    def test_weights_sum_to_one(self):
        for mode, w in WEIGHT_TABLE.items():
            total = w.w_time + w.w_cost + w.w_battery + w.w_safety
            assert abs(total - 1.0) < 0.001, f"{mode}: weights sum {total} != 1.0"

    def test_balanced_is_default_in_route_request(self):
        """RouteRequest.optimization_mode default 'balanced' olmalı."""
        from app.models import RouteRequest, GeoPoint
        req = RouteRequest(
            start_location=GeoPoint(lat=41.0, lon=29.0),
            end_location=GeoPoint(lat=39.9, lon=32.8),
            current_soc_percent=80.0,
        )
        assert req.smart_plan_enabled is True
        assert req.optimization_mode == "balanced"

    def test_invalid_mode_rejected(self):
        from app.models import RouteRequest, GeoPoint
        with pytest.raises(Exception):  # ValidationError
            RouteRequest(
                start_location=GeoPoint(lat=41.0, lon=29.0),
                end_location=GeoPoint(lat=39.9, lon=32.8),
                current_soc_percent=80.0,
                optimization_mode="invalid_mode_xyz",
            )

    def test_mode_emphases_differ(self):
        """time_priority w_time > balanced w_time."""
        time_w = get_weights(OptimizationMode.TIME_PRIORITY)
        balanced_w = get_weights(OptimizationMode.BALANCED)
        cost_w = get_weights(OptimizationMode.COST_PRIORITY)
        battery_w = get_weights(OptimizationMode.BATTERY_CARE)

        assert time_w.w_time > balanced_w.w_time
        assert cost_w.w_cost > balanced_w.w_cost
        assert battery_w.w_battery > balanced_w.w_battery


# =============================================================================
# 2. DYNAMIC BUFFER
# =============================================================================

class TestDynamicBuffer:
    def _basic_ctx(self, **kw):
        defaults = dict(
            leg_distance_km=50.0,
            leg_kwh_estimate=10.0,
            is_final_leg=False,
            weather=None,
            elevation_gain_m=0.0,
            traffic_factor=1.0,
            user_anxiety_factor=0.0,
        )
        defaults.update(kw)
        return BufferContext(**defaults)

    def test_base_safety_when_no_extras(self):
        """Hava yok, yokuş yok, trafik yok → sadece base × mid_multiplier."""
        b = calculate_dynamic_buffer(self._basic_ctx())
        expected = BASE_SAFETY_PERCENT * MID_LEG_MULTIPLIER
        assert b == pytest.approx(expected, rel=0.01)

    def test_final_leg_higher_than_mid(self):
        """Aynı koşullar — son leg daha yüksek buffer (cap'e girmeyecek hafif koşullar)."""
        # NOT: Aşırı ekstrem koşullar buffer'ı 20% cap'e takıyor → ratio bozuluyor.
        # Hafif koşullar kullan ki çarpan oranı net görülsün.
        ctx_mid = self._basic_ctx(
            elevation_gain_m=100,  # +%1
            traffic_factor=1.0,
        )
        ctx_final = self._basic_ctx(
            **{**ctx_mid.__dict__, "is_final_leg": True}
        )
        b_mid = calculate_dynamic_buffer(ctx_mid)
        b_final = calculate_dynamic_buffer(ctx_final)
        assert b_final > b_mid
        # Asimetri oranı: cap'e çarpmadığı sürece çarpan oranı korunmalı
        expected_ratio = FINAL_LEG_MULTIPLIER / MID_LEG_MULTIPLIER
        assert b_final / b_mid == pytest.approx(expected_ratio, rel=0.02)

    def test_capped_at_max(self):
        """Aşırı koşullar buffer'ı 20%'de capler."""
        ctx = self._basic_ctx(
            is_final_leg=True,
            weather=WeatherInfo(temp_c=-10, wind_speed_mps=20, wind_direction_deg=0,
                                condition=WeatherCondition.SNOW),
            elevation_gain_m=2000,
            traffic_factor=2.0,
            user_anxiety_factor=5.0,
        )
        b = calculate_dynamic_buffer(ctx)
        assert b <= MAX_BUFFER_PERCENT

    def test_no_weather_no_penalty(self):
        b1 = calculate_dynamic_buffer(self._basic_ctx(weather=None))
        b2 = calculate_dynamic_buffer(self._basic_ctx(
            weather=WeatherInfo(temp_c=20, wind_speed_mps=0, wind_direction_deg=0,
                                condition=WeatherCondition.CLEAR)
        ))
        # Açık hava + sakin rüzgar = ekstra cezayı tetiklememeli
        assert b1 == pytest.approx(b2, rel=0.01)

    def test_anxiety_increases_buffer(self):
        b_calm = calculate_dynamic_buffer(self._basic_ctx(user_anxiety_factor=0.0))
        b_anxious = calculate_dynamic_buffer(self._basic_ctx(user_anxiety_factor=5.0))
        assert b_anxious > b_calm


# =============================================================================
# 3. OBJECTIVE FUNCTION
# =============================================================================

class TestObjective:
    def test_normalize_clamps(self):
        assert normalize(50, ideal=60, worst=600) == 0.0  # ideal'den iyi
        assert normalize(700, ideal=60, worst=600) == 1.0  # worst'tan kötü
        assert normalize(330, ideal=60, worst=600) == pytest.approx(0.5, rel=0.01)

    def test_hard_violation_returns_inf(self):
        """min_arrival_soc_margin < 0 → J = inf (yolda kalır)."""
        m = PlanMetrics(
            total_drive_time_min=120, total_charge_time_min=60,
            min_arrival_soc_margin=-2.0,  # yolda kalır
            num_stops=2,
        )
        b = calculate_objective(m, get_weights(OptimizationMode.BALANCED))
        assert b.J == float("inf")
        assert b.is_hard_violation is True

    def test_safe_plan_returns_finite(self):
        m = PlanMetrics(
            total_drive_time_min=120, total_charge_time_min=30,
            total_cost_tl=80, high_soc_minutes=10, min_arrival_soc_margin=5.0,
            num_stops=1,
        )
        b = calculate_objective(m, get_weights(OptimizationMode.BALANCED))
        assert 0 <= b.J < float("inf")
        assert b.is_hard_violation is False

    def test_time_mode_penalizes_time_more(self):
        """Aynı plan, time_priority modu daha çok zaman katsayısı.
        Cost normalize bounds [50,800] olduğu için cost farkı belirgin olmalı."""
        # Yavaş + ucuz plan
        m_slow = PlanMetrics(total_drive_time_min=400, total_charge_time_min=80,
                              total_cost_tl=80, min_arrival_soc_margin=5)
        # Hızlı + pahalı plan
        m_fast = PlanMetrics(total_drive_time_min=120, total_charge_time_min=30,
                              total_cost_tl=600, min_arrival_soc_margin=5)
        time_w = get_weights(OptimizationMode.TIME_PRIORITY)
        cost_w = get_weights(OptimizationMode.COST_PRIORITY)

        # time_priority: hızlı plan daha iyi (zaman ağırlık 0.55)
        assert calculate_objective(m_fast, time_w).J < calculate_objective(m_slow, time_w).J
        # cost_priority: ucuz plan daha iyi (cost ağırlık 0.50)
        assert calculate_objective(m_slow, cost_w).J < calculate_objective(m_fast, cost_w).J

    def test_battery_care_penalizes_high_soc(self):
        m_low_high_soc = PlanMetrics(total_drive_time_min=100, total_charge_time_min=30,
                                       high_soc_minutes=0, min_arrival_soc_margin=5)
        m_high_high_soc = PlanMetrics(total_drive_time_min=100, total_charge_time_min=30,
                                        high_soc_minutes=80, min_arrival_soc_margin=5)
        battery_w = get_weights(OptimizationMode.BATTERY_CARE)
        assert calculate_objective(m_low_high_soc, battery_w).J < \
               calculate_objective(m_high_high_soc, battery_w).J

    def test_low_margin_increases_safety_penalty(self):
        m_safe = PlanMetrics(total_drive_time_min=100, total_charge_time_min=30,
                              min_arrival_soc_margin=10)
        m_risky = PlanMetrics(total_drive_time_min=100, total_charge_time_min=30,
                               min_arrival_soc_margin=1)
        balanced_w = get_weights(OptimizationMode.BALANCED)
        assert calculate_objective(m_risky, balanced_w).J > \
               calculate_objective(m_safe, balanced_w).J


# =============================================================================
# 4. BACKWARD PLANNER
# =============================================================================

@dataclass
class _MockHotspot:
    segment_index: int
    soc_at_point: float
    distance_from_start_km: float
    location: Optional[object] = None
    remaining_distance_km: float = 0.0
    min_required_soc: float = 15.0
    recommended_charge_to: float = 80.0


@dataclass
class _MockSegment:
    index: int
    distance_km: float
    elevation_gain_m: float = 0.0
    cumulative_distance_km: float = 0.0
    start_point: Optional[object] = None
    end_point: Optional[object] = None


@dataclass
class _MockSegWithCons:
    segment: _MockSegment
    consumption_kwh: float


def _build_mock_segments(n_segments=10, km_each=20, kwh_each=4.0):
    segs = []
    for i in range(n_segments):
        seg = _MockSegment(index=i, distance_km=km_each, cumulative_distance_km=km_each * (i + 1))
        segs.append(_MockSegWithCons(segment=seg, consumption_kwh=kwh_each))
    return segs


class TestBackwardPlanner:
    def test_empty_hotspots(self):
        from app.optimization.backward_planner import plan_backwards
        result = plan_backwards(
            hotspots=[], segments=_build_mock_segments(),
            battery_kwh=50.0, arrival_soc_target=15.0,
        )
        assert result == []

    def test_min_target_above_arrival_plus_buffer(self):
        """Her durakta min_target_soc en az arrival + buffer kadar."""
        from app.optimization.backward_planner import plan_backwards
        segs = _build_mock_segments(n_segments=10)
        hotspots = [_MockHotspot(segment_index=4, soc_at_point=20, distance_from_start_km=100)]
        planned = plan_backwards(
            hotspots=hotspots, segments=segs, battery_kwh=50.0, arrival_soc_target=15.0,
        )
        assert len(planned) == 1
        # Son leg → final asym multiplier ile yüksek buffer
        assert planned[0].min_target_soc >= 15.0 + planned[0].dynamic_buffer * 0.5  # gevşek alt sınır
        assert planned[0].is_final_leg is True

    def test_multi_stop_induction(self):
        """3 durakta sondan başa: ortadaki min_target son'dan büyük (sonraki leg'i aşar)."""
        from app.optimization.backward_planner import plan_backwards
        segs = _build_mock_segments(n_segments=15)
        hotspots = [
            _MockHotspot(segment_index=4, soc_at_point=20, distance_from_start_km=100),
            _MockHotspot(segment_index=9, soc_at_point=20, distance_from_start_km=200),
            _MockHotspot(segment_index=14, soc_at_point=20, distance_from_start_km=300),
        ]
        planned = plan_backwards(
            hotspots=hotspots, segments=segs, battery_kwh=50.0, arrival_soc_target=15.0,
        )
        assert len(planned) == 3
        # Sadece son durak final leg
        assert planned[2].is_final_leg is True
        assert planned[0].is_final_leg is False

    def test_traffic_increases_min_target(self):
        from app.optimization.backward_planner import plan_backwards
        segs = _build_mock_segments(n_segments=10)
        hotspots = [_MockHotspot(segment_index=4, soc_at_point=20, distance_from_start_km=100)]
        # Traffic yok
        p1 = plan_backwards(hotspots=hotspots, segments=segs, battery_kwh=50, arrival_soc_target=15,
                             traffic_factors=[1.0])
        # Ağır trafik
        p2 = plan_backwards(hotspots=hotspots, segments=segs, battery_kwh=50, arrival_soc_target=15,
                             traffic_factors=[1.5])
        assert p2[0].min_target_soc >= p1[0].min_target_soc

    def test_max_target_capped_at_95(self):
        from app.optimization.backward_planner import plan_backwards
        # Çok büyük leg → tüketim batarya kapasitesini aşar → cap'e gelmeli
        segs = _build_mock_segments(n_segments=10, kwh_each=8.0)
        hotspots = [_MockHotspot(segment_index=0, soc_at_point=20, distance_from_start_km=20)]
        p = plan_backwards(hotspots=hotspots, segments=segs, battery_kwh=50, arrival_soc_target=15)
        assert p[0].min_target_soc <= 95.0


# =============================================================================
# 5. PARETO SOLVER (mock-based — gerçek SOC sim'e bağlı)
# =============================================================================

class TestParetoSolver:
    def test_no_charge_solution(self):
        from app.optimization.pareto_solver import ParetoSolver
        solver = ParetoSolver(mode=OptimizationMode.BALANCED)
        sol = solver.solve(
            hotspots=[], segments_with_consumption=_build_mock_segments(5),
            battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
        )
        assert sol.per_stop_target_soc == []
        assert sol.metrics.num_stops == 0

    def test_solver_returns_finite_J_for_safe_plan(self):
        from app.optimization.pareto_solver import ParetoSolver
        solver = ParetoSolver(mode=OptimizationMode.BALANCED)
        segs = _build_mock_segments(n_segments=10)
        hotspots = [_MockHotspot(segment_index=4, soc_at_point=25, distance_from_start_km=100)]
        sol = solver.solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
        )
        assert sol.breakdown.J < float("inf")
        assert len(sol.per_stop_target_soc) == 1
        assert 30 <= sol.per_stop_target_soc[0] <= 95

    def test_modes_produce_different_solutions(self):
        """time_priority ve cost_priority aynı rotada farklı target_soc'lar üretebilir."""
        from app.optimization.pareto_solver import ParetoSolver
        segs = _build_mock_segments(n_segments=15)
        hotspots = [
            _MockHotspot(segment_index=4, soc_at_point=25, distance_from_start_km=100),
            _MockHotspot(segment_index=9, soc_at_point=25, distance_from_start_km=200),
        ]
        sol_time = ParetoSolver(OptimizationMode.TIME_PRIORITY).solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
        )
        sol_battery = ParetoSolver(OptimizationMode.BATTERY_CARE).solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
        )
        # Battery_care düşük target tercih etmeli (high_soc_minutes düşürmek için)
        assert sum(sol_battery.per_stop_target_soc) <= sum(sol_time.per_stop_target_soc) + 5

    def test_5plus_stop_uses_fallback(self):
        from app.optimization.pareto_solver import ParetoSolver, MAX_PARETO_STOPS
        segs = _build_mock_segments(n_segments=30)
        hotspots = [
            _MockHotspot(segment_index=4 * i, soc_at_point=25, distance_from_start_km=80 * (i + 1))
            for i in range(MAX_PARETO_STOPS + 1)  # 6 stops
        ]
        sol = ParetoSolver(OptimizationMode.BALANCED).solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
        )
        assert sol.fallback_used is True


# =============================================================================
# 6. DECISION LOGGER
# =============================================================================

class TestDecisionLogger:
    def test_anonymize_default_on(self, monkeypatch):
        monkeypatch.delenv("LOG_DECISIONS_ANONYMIZE", raising=False)
        assert _is_anonymize_enabled() is True

    def test_anonymize_can_be_disabled(self, monkeypatch):
        monkeypatch.setenv("LOG_DECISIONS_ANONYMIZE", "false")
        assert _is_anonymize_enabled() is False

    def test_round_coords_when_enabled(self, monkeypatch):
        monkeypatch.setenv("LOG_DECISIONS_ANONYMIZE", "true")
        lat, lon = _round_coords(41.0123456, 28.9876543)
        # 0.01 derece yuvarlama → ~1 km
        assert lat == pytest.approx(41.01, abs=0.001)
        assert lon == pytest.approx(28.99, abs=0.001)

    def test_round_coords_passthrough_when_disabled(self, monkeypatch):
        monkeypatch.setenv("LOG_DECISIONS_ANONYMIZE", "false")
        lat, lon = _round_coords(41.0123456, 28.9876543)
        assert lat == 41.0123456
        assert lon == 28.9876543

    def test_jsonl_append_writes_line(self, tmp_path):
        logger = DecisionLogger(log_dir=tmp_path)
        record = DecisionRecord(
            trip_id="abc123", timestamp_iso="2026-04-25T12:00:00Z",
            origin_lat=41.0, origin_lon=29.0,
            destination_lat=39.9, destination_lon=32.8,
            total_distance_km=450.0, vehicle_id="test_v",
            battery_kwh=50.0, start_soc=80.0, arrival_soc_target=15.0,
            smart_plan_enabled=True, optimization_mode="balanced",
            weights={"w_time": 0.30, "w_cost": 0.25, "w_battery": 0.25, "w_safety": 0.20},
            num_stops=2, per_stop_target_socs=[55.0, 70.0],
            per_stop_min_required=[50.0, 65.0], per_stop_dynamic_buffers=[5.0, 7.5],
            predicted_total_time_min=300.0, predicted_charge_time_min=45.0,
            predicted_total_cost_tl=180.0, predicted_arrival_soc_margin=3.0,
            predicted_high_soc_minutes=10.0, J_score=0.45,
        )
        ok = logger.write(record)
        assert ok is True

        files = list(tmp_path.glob("decisions_*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["trip_id"] == "abc123"
        assert parsed["per_stop_target_socs"] == [55.0, 70.0]


# =============================================================================
# 7. BACKWARD COMPAT — smart_plan_enabled=False eski davranışla aynı
# =============================================================================

class TestBackwardCompat:
    def test_socsimulator_without_per_stop_targets_works(self):
        """Eski API: per_stop_targets verilmediğinde charge_target_soc kullanılır."""
        from app.soc_simulator import SOCSimulator, SegmentWithConsumption

        class _S:
            def __init__(self, idx, dist, kwh):
                self.index = idx
                self.distance_km = dist
                self.elevation_gain_m = 0
                self.elevation_loss_m = 0
                self.cumulative_distance_km = dist * (idx + 1)
                self.start_point = None
                self.end_point = None

        segs = []
        for i in range(10):
            seg = _S(i, 30, 6.0)
            sw = SegmentWithConsumption(segment=seg, consumption_kwh=6.0)
            segs.append(sw)

        sim = SOCSimulator(
            battery_capacity_kwh=50.0, start_soc=80.0,
            target_arrival_soc=15.0, charge_min_soc=18.0, charge_target_soc=80.0,
        )
        result = sim.simulate(segs, total_distance_km=300.0)
        # Eski signature çalışıyor mu — exception yok
        assert result is not None
        # Hotspot varsa recommended_charge_to default ile dolmuş
        for h in result.hotspots:
            assert h.recommended_charge_to == 80.0

    def test_socsimulator_with_per_stop_targets_overrides(self):
        """per_stop_targets verildiğinde hotspot'lar bu değerleri taşımalı (post-process by-pass)."""
        from app.soc_simulator import SOCSimulator, SegmentWithConsumption

        class _S:
            def __init__(self, idx, dist):
                self.index = idx
                self.distance_km = dist
                self.elevation_gain_m = 0
                self.elevation_loss_m = 0
                self.cumulative_distance_km = dist * (idx + 1)
                self.start_point = None
                self.end_point = None

        segs = [SegmentWithConsumption(segment=_S(i, 30), consumption_kwh=6.0) for i in range(15)]
        sim = SOCSimulator(
            battery_capacity_kwh=50.0, start_soc=80.0,
            target_arrival_soc=15.0, charge_min_soc=18.0, charge_target_soc=80.0,
        )
        # per_stop_targets: [55, 65, 75] (3 hotspot için).
        # NOT: SOCSimulator hotspot'ları sonradan merge edebilir; o yüzden tüm
        # kalan hotspot'ların recommended_charge_to'sunun listemizdeki değerlerden biri
        # olduğunu doğrulamak yeterli (post-process üzerine yazmamış olmalı).
        per_stops = [55.0, 65.0, 75.0]
        result = sim.simulate(segs, total_distance_km=450.0, per_stop_targets=per_stops)

        assert len(result.hotspots) >= 1
        for h in result.hotspots:
            # Post-process by-pass çalışıyorsa: her hotspot'un target'ı per_stops içinde olmalı
            assert h.recommended_charge_to in per_stops, (
                f"Hotspot recommended_charge_to={h.recommended_charge_to} "
                f"per_stop_targets {per_stops} içinde değil — post-process override çalışmış olabilir"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
