from app.consumption_engine.vehicle_models import VehicleModel
from app.consumption_engine.v1_rule_based import elevation_layer, weather_layer, load_layer
from app.utils.logger import get_logger

logger = get_logger("main_calculator")


def calculate_segment_consumption_kwh(
    vehicle: VehicleModel,
    segment_distance_km: float,
    segment_elevation_gain_m: float,
    temperature_celsius: float,
    extra_load_kg: int,
    engine_version: str = "v1"  # V1.3 Strateji Deseni
) -> float:
    """
    Tüm katmanları kullanarak 1 segmentlik net tüketimi kWh olarak hesaplar.
    Bu, 'route_planner'ın çağıracağı tek arayüzdür.
    """

    if engine_version == "v1":
        try:
            # --- BAZ HESAPLAMALAR ---
            base_consumption_kwh = (vehicle.base_consumption_wh_km / 1000) * segment_distance_km

            # --- KATMAN 1 & 2: ÇARPANLAR (YÜK VE HAVA) ---
            load_multiplier = load_layer.get_load_efficiency_multiplier(extra_load_kg)
            weather_multiplier = weather_layer.get_weather_efficiency_multiplier(temperature_celsius)

            adjusted_base_consumption_kwh = base_consumption_kwh * load_multiplier * weather_multiplier

            # --- KATMAN 3: FİZİKSEL MALİYET (YOKUŞ) ---
            total_weight_kg = load_layer.get_total_weight_kg(vehicle, extra_load_kg)
            elevation_energy_kwh = elevation_layer.calculate_elevation_energy_kwh(
                total_weight_kg=total_weight_kg,
                elevation_gain_meters=segment_elevation_gain_m
            )

            # --- FİNAL HESAPLAMA ---
            net_consumption_kwh = adjusted_base_consumption_kwh + elevation_energy_kwh

            # Minimum güvenlik limiti
            MIN_CONSUMPTION_KWH_PER_KM = 0.010  # (10 Wh/km)
            min_consumption = MIN_CONSUMPTION_KWH_PER_KM * segment_distance_km

            return max(min_consumption, net_consumption_kwh)

        except Exception as e:
            logger.error("V1 Kural Motoru hatası", error=str(e))
            return (vehicle.base_consumption_wh_km / 1000) * segment_distance_km * 1.5

    elif engine_version == "v2":
        logger.info("V2 motoru çağrıldı, ancak henüz uygulanmadı.")
        raise NotImplementedError("V2 ML Tüketim Motoru henüz entegre edilmedi.")

    else:
        raise ValueError(f"Bilinmeyen motor versiyonu: {engine_version}")
