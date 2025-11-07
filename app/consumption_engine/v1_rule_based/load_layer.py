from app.consumption_engine.vehicle_models import VehicleModel

# Basit bir kural: Her 100kg ekstra yük, tüketimi %3 artırır (bu bir V1 tahminidir)
LOAD_PENALTY_FACTOR_PER_100KG = 0.03


def get_total_weight_kg(vehicle: VehicleModel, extra_load_kg: int) -> int:
    """Aracın baz ağırlığı ile ekstra yükü toplar."""
    return vehicle.base_weight_kg + extra_load_kg


def get_load_efficiency_multiplier(extra_load_kg: int) -> float:
    """Ekstra yüke göre tüketim artış çarpanını hesaplar (1.0 = %0 etki)."""
    if extra_load_kg <= 0:
        return 1.0

    penalty = (extra_load_kg / 100) * LOAD_PENALTY_FACTOR_PER_100KG
    return 1.0 + penalty
