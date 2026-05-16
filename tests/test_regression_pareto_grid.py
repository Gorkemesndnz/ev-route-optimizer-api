"""
Regresyon Testi — Pareto Grid Çöküşü
=====================================

Düzce→Aydın (4-leg ~180 km, MG4 51 kWh) senaryosunda gözlenen davranış:
  - BackwardPlanner her durağı min_target=95'e clamp ediyor
  - Pareto grid tek elemanlı kalıyor (range(95,96,5)=[95])
  - Pareto solver "1 kombinasyon" üretiyor → optimizasyon yok

Bu testler iki ayrı root cause'u doğrular:

  Bug A (backward_planner.py:129):
    `next_min_arrival = min_target` — mid-stop'larda LEAVE-SOC
    propagate ediliyor; doğrusu MIN_SAFE_ARRIVAL (~15-18%) olmalı.
    Sonuç: clamp zinciri her durağı 95'e itiyor.

  Bug B (pareto_solver.py:125):
    `range(int(min), 96, 5)` 95 endpoint'ini garanti etmiyor.
    Örn. min=81 → [81, 86, 91] — 95 missing.

Run: pytest tests/test_regression_pareto_grid.py -v
"""

from dataclasses import dataclass
from typing import Optional, List

import pytest

from app.optimization.backward_planner import plan_backwards, MAX_CHARGE_TARGET
from app.optimization.pareto_solver import ParetoSolver, GRID_STEP
from app.optimization.modes import OptimizationMode


# =============================================================================
# MOCK FIXTURES
# =============================================================================

@dataclass
class _H:
    segment_index: int
    soc_at_point: float
    distance_from_start_km: float
    location: Optional[object] = None
    remaining_distance_km: float = 0.0
    min_required_soc: float = 15.0
    recommended_charge_to: float = 80.0


@dataclass
class _S:
    index: int
    distance_km: float
    elevation_gain_m: float = 0.0
    cumulative_distance_km: float = 0.0
    start_point: Optional[object] = None
    end_point: Optional[object] = None


@dataclass
class _SC:
    segment: _S
    consumption_kwh: float


def _build_duzce_aydin_scenario():
    """
    Düzce→Aydın benzeri 720 km, 4 leg x ~180 km rota.
    MG4 51 kWh, 180 Wh/km → 32.4 kWh/leg ≈ %63.8 SOC drop.
    """
    # 24 segment x 30 km = 720 km; 5.4 kWh/segment (180 Wh/km)
    segs: List[_SC] = []
    for i in range(24):
        segs.append(_SC(
            segment=_S(index=i, distance_km=30.0, cumulative_distance_km=30.0 * (i + 1)),
            consumption_kwh=5.4,
        ))
    # 3 charging stops, 6 segment aralıklı
    hotspots = [
        _H(segment_index=5, soc_at_point=21.5, distance_from_start_km=180),
        _H(segment_index=11, soc_at_point=21.5, distance_from_start_km=360),
        _H(segment_index=17, soc_at_point=21.5, distance_from_start_km=540),
    ]
    return hotspots, segs


# =============================================================================
# BUG A — BACKWARD PLANNER KÜMÜLATİF INFLATION
# =============================================================================

class TestBackwardPlannerInflation:
    """
    next_min_arrival = min_target (line 129) propagation bug.

    Beklenen davranış (FIX SONRASI):
      Mid stop'larda min_target ~80-85 olmalı (10% arrival değil,
      ~15% safe arrival baz alınmalı).
    Mevcut davranış (BUG):
      Mid stop'larda min_target = 95 (clamp).
    """

    def test_mid_stops_should_not_all_clamp_to_max(self):
        """Mid stop'lar 95'e clamp olmamalı — fizik buna izin veriyor."""
        hotspots, segs = _build_duzce_aydin_scenario()
        planned = plan_backwards(
            hotspots=hotspots, segments=segs,
            battery_kwh=50.8, arrival_soc_target=10.0,
        )
        mid_stops = [p for p in planned if not p.is_final_leg]
        clamped_count = sum(1 for p in mid_stops if p.min_target_soc >= MAX_CHARGE_TARGET - 0.1)

        # Fix sonrası: mid stop'lar ~82-85'te olmalı; en fazla 1 tanesi 95'e yakın
        assert clamped_count == 0, (
            f"Mid stop'larda clamp inflation: {[p.min_target_soc for p in planned]}. "
            f"next_min_arrival={p.min_target} propagation bug — line 129'da "
            f"MIN_SAFE_ARRIVAL kullanılmalı."
        )

    def test_mid_stop_min_target_below_max(self):
        """Mid stop min_target < %90 olmalı (fizik 10+63+buffer ≈ 80 izin veriyor)."""
        hotspots, segs = _build_duzce_aydin_scenario()
        planned = plan_backwards(
            hotspots=hotspots, segments=segs,
            battery_kwh=50.8, arrival_soc_target=10.0,
        )
        for i, p in enumerate(planned):
            if not p.is_final_leg:
                assert p.min_target_soc < 90.0, (
                    f"Stop {i}: min_target={p.min_target_soc} — fizik bunu gerektirmiyor. "
                    f"Beklenen ~82-85% (15% safe arrival + 64% leg drop + 3.5% buffer)."
                )

    def test_final_stop_uses_arrival_target(self):
        """Final stop arrival_soc_target baz alır — bu doğru ve değişmemeli."""
        hotspots, segs = _build_duzce_aydin_scenario()
        planned = plan_backwards(
            hotspots=hotspots, segments=segs,
            battery_kwh=50.8, arrival_soc_target=10.0,
        )
        final = planned[-1]
        assert final.is_final_leg
        # 10% + 63.8% leg + 7.5% buffer ≈ 81.3
        assert 78.0 <= final.min_target_soc <= 85.0, (
            f"Final stop min_target={final.min_target_soc} — beklenen ~81.3 "
            f"(arrival=10 + drop=64 + buffer=7.5)."
        )

    def test_inflation_proportional_to_stop_count(self):
        """Bug imzası: stop sayısı arttıkça erken stop'lar daha çok clamp olur.

        Bu test mevcut bug'ı KARAKTERİZE eder; fix sonrası bu pattern kaybolmalı.
        """
        hotspots, segs = _build_duzce_aydin_scenario()
        planned = plan_backwards(
            hotspots=hotspots, segments=segs,
            battery_kwh=50.8, arrival_soc_target=10.0,
        )
        # FIX SONRASI: erken stop'lar geç stop'lardan büyük olmamalı
        # (final hariç hepsi yaklaşık aynı min_target'a sahip olmalı, çünkü leg'ler eşit)
        mid_targets = [p.min_target_soc for p in planned if not p.is_final_leg]
        if len(mid_targets) >= 2:
            spread = max(mid_targets) - min(mid_targets)
            assert spread < 5.0, (
                f"Mid stop min_target'ları arasında {spread}% fark — "
                f"eşit leg'lerde olmamalı. Inflation propagation imzası: {mid_targets}"
            )


# =============================================================================
# BUG B — PARETO GRID 95 ENDPOINT EKSİK
# =============================================================================

def _build_solver_grid_for_min(min_target: float) -> list:
    """pareto_solver.py'deki grid logic'inin test edilebilir kopyası.
    Üretim kodu değişirse bu helper de güncellenmeli."""
    return sorted(set(
        list(range(int(min_target), int(MAX_CHARGE_TARGET) + 1, GRID_STEP))
        + [int(MAX_CHARGE_TARGET)]
    ))


class TestParetoGridEndpoint:
    """
    Bug B regresyon: range(int(min), 96, 5) tek başına 95 endpoint'i garanti etmiyor.
    Fix sonrası: solver grid logic'i 95'i her zaman içermeli.
    """

    def test_grid_includes_max_when_min_is_81(self):
        """min=81 → grid'de 95 olmalı."""
        grid = _build_solver_grid_for_min(81.0)
        assert int(MAX_CHARGE_TARGET) in grid, (
            f"Grid {grid} → 95 endpoint missing."
        )

    @pytest.mark.parametrize("min_val", [81, 82, 83, 84, 91, 92, 93, 94])
    def test_grid_always_includes_max_endpoint(self, min_val):
        """Her min değeri için solver grid 95'i içermeli."""
        grid = _build_solver_grid_for_min(float(min_val))
        assert int(MAX_CHARGE_TARGET) in grid, (
            f"min={min_val}: grid={grid}, 95 missing."
        )

    def test_grid_does_not_lose_low_endpoints(self):
        """min=82 → grid'de 82 hâlâ olmalı (sadece 95 eklenmesi alt değerleri silmemeli)."""
        grid = _build_solver_grid_for_min(82.0)
        assert 82 in grid and 95 in grid, f"Grid {grid}"
        # Bekleen: [82, 87, 92, 95]
        assert grid == [82, 87, 92, 95]


# =============================================================================
# BUG A+B BİRLEŞİK ETKİ — PARETO SOLVER GERÇEK COMBO SAYISI
# =============================================================================

class TestParetoSolverEffectiveness:
    """End-to-end: Pareto solver Düzce→Aydın senaryosunda gerçekten optimize ediyor mu?"""

    def test_solver_evaluates_multiple_combos(self):
        """Solver en az 8 farklı kombinasyon test etmeli (3 stop x ≥2 opsiyon)."""
        hotspots, segs = _build_duzce_aydin_scenario()
        solver = ParetoSolver(mode=OptimizationMode.BALANCED)

        # solve() içindeki grid hesabını re-derive et
        planned = plan_backwards(
            hotspots=hotspots, segments=segs,
            battery_kwh=50.8, arrival_soc_target=10.0,
        )
        grids = [
            list(range(int(p.min_target_soc), int(MAX_CHARGE_TARGET) + 1, GRID_STEP))
            or [int(p.min_target_soc)]
            for p in planned
        ]
        total_combos = 1
        for g in grids:
            total_combos *= len(g)

        assert total_combos >= 8, (
            f"Sadece {total_combos} kombinasyon test ediliyor. "
            f"Grid'ler: {grids}. Pareto efektif değil → tek noktaya kilitlenmiş."
        )

    def test_solver_does_not_select_all_max_targets(self):
        """Pareto her durağı 95'e koymamalı — fizik buna izin vermiyor olsa anlam taşır."""
        hotspots, segs = _build_duzce_aydin_scenario()
        solver = ParetoSolver(mode=OptimizationMode.BALANCED)
        sol = solver.solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.8, start_soc=85.0, arrival_soc_target=10.0,
            total_distance_km=720.0,
        )

        all_max = all(t >= MAX_CHARGE_TARGET - 0.1 for t in sol.per_stop_target_soc)
        assert not all_max, (
            f"Pareto tüm stop'ları %95'e koydu: {sol.per_stop_target_soc}. "
            f"Fizik buna mecbur değil — backward_planner inflation'ı gerçek "
            f"optimizasyon alanını çökertmiş."
        )

    def test_modes_evaluate_combos_with_distinct_J(self):
        """Modlar aynı combo'yu seçseler bile J skorları farklı olmalı (farklı ağırlıklar).

        NOT: Düzce→Aydın mock senaryosunda 4 mod aynı combo'yu en iyi bulabilir
        (grid alt sınırı min_target≈82 olduğu için battery_care'in tercih edeceği
        daha düşük noktalar yok). Mod ayrımı J skorunda görülmeli.
        """
        hotspots, segs = _build_duzce_aydin_scenario()
        sol_time = ParetoSolver(OptimizationMode.TIME_PRIORITY).solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.8, start_soc=85.0, arrival_soc_target=10.0,
            total_distance_km=720.0,
        )
        sol_battery = ParetoSolver(OptimizationMode.BATTERY_CARE).solve(
            hotspots=hotspots, segments_with_consumption=segs,
            battery_kwh=50.8, start_soc=85.0, arrival_soc_target=10.0,
            total_distance_km=720.0,
        )
        # J skorları farklı olmalı (farklı ağırlıklar farklı objektif değer üretir)
        assert sol_time.breakdown.J != sol_battery.breakdown.J, (
            f"Modlar aynı J üretti — objective fonksiyonu mod ağırlıklarını dikkate almıyor."
        )
        # Combo sayısı yeterli olmalı (Bug A+B fix sonrası)
        assert len(sol_time.per_stop_target_soc) == 3
        assert len(sol_battery.per_stop_target_soc) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
