from app.infrastructure.vehicle_catalog import get_vehicle_model
from app.consumption_engine.main_calculator import calculate_segment_consumption_kwh

v = get_vehicle_model('tesla_3_long_range_dual_motor_2023')
print("Base WH/KM:", v.base_consumption_wh_km)
print("Base KWH/100KM:", getattr(v, "base_consumption_kwh_per_100km", None))

c = calculate_segment_consumption_kwh(v, 671.0)
print("Consumption for 671km:", c)
