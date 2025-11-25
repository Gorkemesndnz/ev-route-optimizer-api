from dataclasses import dataclass


@dataclass
class VehicleModel:
    """
    ✅ V1.6 - MainCalculator ile %100 uyumlu araç veri yapısı
    
    🔥 V1.6 GÜNCELLEME:
    - base_weight_kg → curb_weight_kg (MainCalculator uyumu)
    - auxiliary_power_kw eklendi (HVAC hesaplamaları için)
    - Tüm alanlar MainCalculator beklentileriyle eşleşiyor
    """
    model_name: str

    # Fiziksel özellikler
    curb_weight_kg: int               # ✅ V1.6: base_weight_kg → curb_weight_kg
    battery_capacity_kwh: float        # kullanılabilir kapasite (kWh)

    # Tüketim (Wh/km) → MainCalculator'da kwh/100km'e çevriliyor
    base_consumption_wh_km: float     

    # Yardımcı sistemler (HVAC, farlar, elektronik)
    auxiliary_power_kw: float = 1.2   # ✅ V1.6: HVAC hesaplamaları için

    # Şarj özellikleri
    connector_type: str               # "CCS" | "CHAdeMO" | "Type2"
    avg_dc_charge_rate_kw: float      # 10 → 80 arası ortalama
    avg_ac_charge_rate_kw: float

VEHICLE_DB = {
    "mg4_51kwh": VehicleModel(
        model_name="MG4 Electric 51 kWh",
        curb_weight_kg=1736,             # ✅ V1.6: base_weight_kg → curb_weight_kg
        battery_capacity_kwh=50.8,
        base_consumption_wh_km=159.0,    # 15.9 kWh/100km
        auxiliary_power_kw=1.2,          # ✅ V1.6: HVAC için
        connector_type="CCS",
        avg_dc_charge_rate_kw=85.0,      # gerçek ortalama
        avg_ac_charge_rate_kw=11.0,
    ),

    "tesla_model_3_long_range": VehicleModel(
        model_name="Tesla Model 3 Long Range",
        curb_weight_kg=1847,             # ✅ V1.6: base_weight_kg → curb_weight_kg
        battery_capacity_kwh=75.0,       # usable
        base_consumption_wh_km=145.0,    # 14.5 kWh/100km
        auxiliary_power_kw=1.0,          # ✅ V1.6: Tesla daha verimli HVAC
        connector_type="CCS",
        avg_dc_charge_rate_kw=250.0,
        avg_ac_charge_rate_kw=11.0,
    ),

    "opel_frontera_44": VehicleModel(
        model_name="Opel Frontera Electric 44 kWh",
        curb_weight_kg=1589,             # ✅ V1.6: base_weight_kg → curb_weight_kg
        battery_capacity_kwh=43.8,
        base_consumption_wh_km=183.0,    # 18.3 kWh/100km (Real)
        auxiliary_power_kw=1.3,          # ✅ V1.6: Opel daha yüksek HVAC
        connector_type="CCS",
        avg_dc_charge_rate_kw=60.0,      # avg based on 10→80 data
        avg_ac_charge_rate_kw=7.4,
    ),
}


def get_vehicle_model(model_id: str) -> VehicleModel:
    model = VEHICLE_DB.get(model_id)
    if model is None:
        raise ValueError(f"Unknown vehicle model: {model_id}")
    return model
