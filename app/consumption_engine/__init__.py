"""
ConsumptionEngine — Factory & Public API

Kullanım:
    from app.consumption_engine import get_engine
    engine = get_engine()
    results = engine.estimate(vehicle, segments, ...)
"""

from app.consumption_engine.base import ConsumptionEngine
from app.constants import USE_ML_ENGINE


def get_engine() -> ConsumptionEngine:
    """
    Feature flag'e göre doğru tüketim motorunu döndürür.
    
    USE_ML_ENGINE = False → PhysicsConsumptionEngine (MainCalculator proxy)
    USE_ML_ENGINE = True  → MLConsumptionEngine (şimdilik stub)
    """
    if USE_ML_ENGINE:
        from app.consumption_engine.ml_stub_engine import MLConsumptionEngine
        return MLConsumptionEngine()
    else:
        from app.consumption_engine.physics_engine import PhysicsConsumptionEngine
        return PhysicsConsumptionEngine()
