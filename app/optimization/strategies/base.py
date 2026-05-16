"""
ChargeTargetStrategy — Interface ve Factory
============================================

Eskiden orchestrator._run_soc_simulation içinde 3 yollu if/elif vardı:
- Path A: user_target_soc_override → SOCSimulator(user_override_target=True)
- Path B: smart_plan=False → ChargePlanOptimizer.find_optimal_plan()
- Path C: Pareto → _run_pareto_simulation()

Bu refactor 3 path'ı tek bir Strategy interface altına alır:
- Test edilebilirlik: her strategy bağımsız mock'lanabilir
- Eklenebilirlik: yeni bir strategy (ör. ML-based) eklemek kolay
- Tek dönüş tipi (ChargingPlan) — orchestrator artık ne yapıldığını bilmez

Refactor 1 — 2026-05-05.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ChargingContext:
    """
    Bir strategy'nin plan üretebilmesi için ihtiyaç duyduğu tüm girdiler.

    Bu sınıf RouteRequest'e bağımlı DEĞİLDİR — orchestrator çağırırken
    RouteRequest'ten ilgili alanları çıkartıp ChargingContext'i doldurur.
    Strategy'ler RouteRequest tipini bilmek zorunda kalmaz.
    """
    # --- Simülasyon girdileri ---
    segments_with_consumption: List[Any]   # List[SegmentWithConsumption]
    total_distance_km: float
    battery_kwh: float
    start_soc: float
    arrival_soc: float
    charge_min_soc: float
    charge_target_soc: float                # default fallback (user override veya 80)
    avg_speed_kmh: float

    # --- Strategy seçim girdileri ---
    smart_plan_enabled: bool = False
    user_target_soc_override: Optional[float] = None
    optimization_mode: str = "balanced"     # OptimizationMode değeri ("balanced" / "fast" / "cheap" / "battery")

    # --- Decision log meta (sadece Pareto kullanır, opsiyonel) ---
    trip_id: Optional[str] = None
    origin_lat: Optional[float] = None
    origin_lon: Optional[float] = None
    destination_lat: Optional[float] = None
    destination_lon: Optional[float] = None
    vehicle_id: str = "unknown"


@dataclass
class ChargingPlan:
    """
    Strategy çıktısı — uniform kontrat.

    Eskiden _run_soc_simulation farklı path'larda farklı sayıda değer
    döndürüyordu (charge_target_soc, sim_result). Bu sınıf tek tip dönüş sağlar.
    """
    charge_target_soc: float                # Geriye uyumlu temsili target (max veya sabit)
    sim_result: Any                         # SimulationResult
    per_stop_targets: List[float] = field(default_factory=list)  # boş liste = stop yok / homojen
    strategy_name: str = "unknown"          # "manual" / "grid_search" / "pareto"
    fallback_used: bool = False             # Strategy fallback'e düştü mü? (Pareto greedy gibi)
    metadata: Dict[str, Any] = field(default_factory=dict)       # decision log breakdown vs.


# =============================================================================
# INTERFACE
# =============================================================================

class ChargeTargetStrategy(ABC):
    """
    Şarj hedef stratejisi interface'i.

    Tüm implementation'lar plan(ctx) -> ChargingPlan imzasını uygular.
    State-less olmaları beklenir; gerekirse kendi içinde solver/optimizer instantiate eder.
    """

    @abstractmethod
    def plan(self, ctx: ChargingContext) -> ChargingPlan:
        """
        Bağlam bilgisinden bir şarj planı üret.

        Args:
            ctx: Strategy için tüm girdiler.

        Returns:
            ChargingPlan: Uniform plan çıktısı.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy adı (logger ve telemetri için)."""
        ...


# =============================================================================
# FACTORY
# =============================================================================

def select_strategy(ctx: ChargingContext) -> ChargeTargetStrategy:
    """
    Bağlama göre uygun strategy'yi seç.

    Karar mantığı (eski _run_soc_simulation ile aynı):
    1. user_target_soc_override varsa → ManualOverrideStrategy
    2. smart_plan_enabled=False → GridSearchStrategy (legacy ChargePlanOptimizer)
    3. Aksi halde → ParetoStrategy

    Lazy import: circular dependency önler (strategies → solver → ...).
    """
    # Lazy imports
    from app.optimization.strategies.manual import ManualOverrideStrategy
    from app.optimization.strategies.grid_search import GridSearchStrategy
    from app.optimization.strategies.pareto import ParetoStrategy

    if ctx.user_target_soc_override is not None:
        return ManualOverrideStrategy()
    if not ctx.smart_plan_enabled:
        return GridSearchStrategy()
    return ParetoStrategy()
