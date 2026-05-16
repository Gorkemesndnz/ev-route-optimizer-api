"""
Optimization Modes — Pareto Ağırlık Tabloları
==============================================

4 mod: balanced (default), time_priority, cost_priority, battery_care.
Her mod için (w_time, w_cost, w_battery, w_safety) ağırlık vektörü.
"""

from enum import Enum
from typing import Dict
from pydantic import BaseModel, Field, field_validator


class OptimizationMode(str, Enum):
    """Pareto karar modu — RouteRequest.optimization_mode."""
    BALANCED = "balanced"
    TIME_PRIORITY = "time_priority"
    COST_PRIORITY = "cost_priority"
    BATTERY_CARE = "battery_care"


class ParetoWeights(BaseModel):
    """
    J(plan) = w_time·T + w_cost·C + w_battery·E + w_safety·S
    Ağırlıklar toplamı 1.0 olmalı.
    """
    w_time: float = Field(..., ge=0.0, le=1.0)
    w_cost: float = Field(..., ge=0.0, le=1.0)
    w_battery: float = Field(..., ge=0.0, le=1.0)
    w_safety: float = Field(..., ge=0.0, le=1.0)

    @field_validator("w_safety")
    @classmethod
    def _check_sum(cls, v: float, info) -> float:
        total = v + info.data.get("w_time", 0) + info.data.get("w_cost", 0) + info.data.get("w_battery", 0)
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Pareto weights must sum to 1.0 (got {total:.4f})")
        return v


WEIGHT_TABLE: Dict[OptimizationMode, ParetoWeights] = {
    OptimizationMode.BALANCED:      ParetoWeights(w_time=0.30, w_cost=0.25, w_battery=0.25, w_safety=0.20),
    OptimizationMode.TIME_PRIORITY: ParetoWeights(w_time=0.55, w_cost=0.15, w_battery=0.15, w_safety=0.15),
    OptimizationMode.COST_PRIORITY: ParetoWeights(w_time=0.20, w_cost=0.50, w_battery=0.15, w_safety=0.15),
    OptimizationMode.BATTERY_CARE:  ParetoWeights(w_time=0.20, w_cost=0.15, w_battery=0.50, w_safety=0.15),
}


def get_weights(mode: OptimizationMode) -> ParetoWeights:
    """Verilen mod için ağırlık vektörünü döndür."""
    return WEIGHT_TABLE[mode]
