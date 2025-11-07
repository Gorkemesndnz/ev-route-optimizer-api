from app.utils.config_manager import config
from app.utils.logger import get_logger

CO2_KG_PER_LITER_GASOLINE = 2.31
logger = get_logger("sustainability_calculator")

def calculate_co2_savings(total_distance_km: float) -> float:
    """Elektrikli araç kullanımında tasarruf edilen CO2 miktarını (kg) hesaplar."""
    try:
        co2_avg_ice_l_per_100km = config.get_co2_avg_ice_consumption()
        region_multiplier = config.get_co2_region_multiplier_tr()
        total_liters = (co2_avg_ice_l_per_100km / 100.0) * total_distance_km
        total_co2_kg_produced = total_liters * CO2_KG_PER_LITER_GASOLINE * region_multiplier
        return round(total_co2_kg_produced, 2)
    except Exception as e:
        logger.error("CO2 hesaplama hatası", error=str(e))
        return 0.0
