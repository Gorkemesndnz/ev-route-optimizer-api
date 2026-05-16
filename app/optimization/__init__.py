"""
Optimization Package — Pareto Karar Mekanizması (Faz 2)
========================================================

Greedy "her durakta %80'e şarj" yerine, kullanıcı modu (time/cost/battery/balanced)
bazlı çok-kriterli optimizasyon yapar.

Modüller:
- modes: OptimizationMode enum + ParetoWeights
- dynamic_buffer: leg-bazlı dinamik güvenlik payı
- backward_planner: sondan başa min_target_soc indüksiyonu
- objective: J(plan) Pareto skor fonksiyonu
- pareto_solver: ana orkestratör
- decision_logger: JSONL karar logu (sonra ML için)
"""

from app.optimization.modes import (
    OptimizationMode,
    ParetoWeights,
    get_weights,
    WEIGHT_TABLE,
)

__all__ = [
    "OptimizationMode",
    "ParetoWeights",
    "get_weights",
    "WEIGHT_TABLE",
]
