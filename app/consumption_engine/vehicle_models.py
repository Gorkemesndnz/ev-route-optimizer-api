from dataclasses import dataclass


@dataclass
class VehicleModel:
    """Elektrikli araçların temel fabrika verilerini tutar."""
    model_name: str
    base_weight_kg: int  # Aracın boş ağırlığı (Weight Unladen)
    battery_capacity_kwh: float  # Kullanılabilir batarya kapasitesi (Useable Capacity)
    base_consumption_wh_km: float  # Gerçekçi baz tüketim (EVDB Real Range Consumption)
    connector_type: str  # Hızlı şarj portu (Fast Charging Charge Port)
    avg_dc_charge_rate_kw: float  # Ortalama DC şarj hızı (10-80% arası)
    avg_ac_charge_rate_kw: float  # Ev/Destinasyon AC şarj hızı


# Gerçek MG4 51kWh verilerini (gönderilen fabrika verilerinden) ekliyoruz
VEHICLE_DB = {
    "mg4_51kwh": VehicleModel(
        model_name="MG4 Electric 51 kWh",
        base_weight_kg=1736,
        battery_capacity_kwh=50.8,
        base_consumption_wh_km=159.0,
        connector_type="CCS",
        avg_dc_charge_rate_kw=85.0,
        avg_ac_charge_rate_kw=11.0,
    ),
    # Gelecekte başka bir araç eklersek:
    # "tesla_y_lr": VehicleModel(...)
}


def get_vehicle_model(model_id: str) -> VehicleModel | None:
    """Verilen model ID'sine göre araç verisini döndürür."""
    return VEHICLE_DB.get(model_id)
