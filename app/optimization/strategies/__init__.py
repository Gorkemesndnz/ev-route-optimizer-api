"""
ChargeTargetStrategy paketi
============================

Tek interface, üç implementation:
- ManualOverrideStrategy: kullanıcı override (sabit target)
- GridSearchStrategy:     legacy ChargePlanOptimizer (75-95 grid)
- ParetoStrategy:         Pareto multi-objective

Kullanım:
    from app.optimization.strategies import (
        ChargingContext, ChargingPlan, select_strategy
    )

    ctx = ChargingContext(...)
    strategy = select_strategy(ctx)
    plan = strategy.plan(ctx)
"""

from app.optimization.strategies.base import (
    ChargingContext,
    ChargingPlan,
    ChargeTargetStrategy,
    select_strategy,
)
from app.optimization.strategies.manual import ManualOverrideStrategy
from app.optimization.strategies.grid_search import GridSearchStrategy
from app.optimization.strategies.pareto import ParetoStrategy

__all__ = [
    "ChargingContext",
    "ChargingPlan",
    "ChargeTargetStrategy",
    "select_strategy",
    "ManualOverrideStrategy",
    "GridSearchStrategy",
    "ParetoStrategy",
]
