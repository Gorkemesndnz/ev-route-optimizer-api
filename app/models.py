from pydantic import BaseModel
from typing import List, Literal, Union


class RouteRequest(BaseModel):
    """API'ye /optimize_route endpoint'i için gelecek olan istek modeli."""
    start_location: str  # Örn: "İstanbul, Türkiye"
    end_location: str  # Örn: "Ankara, Türkiye"
    vehicle_model_id: str  # Örn: "mg4_51kwh" (vehicle_models.py'deki anahtar)
    initial_soc_percent: int  # Mevcut şarj (örn: 85)
    extra_load_kg: int  # Ekstra yük (örn: 150)


class WeatherPrediction(BaseModel):
    """Bir istasyona varıştaki tahmini hava durumu."""
    temperature_celsius: int
    condition_icon: str  # Örn: "rainy", "clear-day"
    description: str


class DriveLeg(BaseModel):
    """Planın bir 'Sürüş' bacağını temsil eder."""
    type: Literal["drive"] = "drive"
    start_point: str
    end_point: str
    distance_km: float
    duration_minutes: int
    start_soc_percent: int  # Bu bacağa BAŞLARKEN şarj durumu
    arrival_soc_percent: int  # Bu bacağı BİTİRİRKEN şarj durumu
    route_polyline: str  # Google'dan gelen kodlanmış rota
    consumption_kwh: float


class ChargeLeg(BaseModel):
    """Planın bir 'Şarj' bacağını temsil eder."""
    type: Literal["charge"] = "charge"
    station_name: str
    station_id: str
    charge_speed_type: Literal["slow", "fast"]  # 'slow' (AC) veya 'fast' (DC)
    arrival_soc_percent: int  # İstasyona GELDİĞİNDEKİ şarj durumu
    target_soc_percent: int  # İstasyondan AYRILACAĞI hedef şarj
    charge_added_kwh: float  # Ne kadar enerji eklendiği
    charge_duration_minutes: int
    predicted_weather_at_arrival: WeatherPrediction


class MultiStopRouteResponse(BaseModel):
    """API'den kullanıcıya dönecek olan tam rota planı modeli."""
    status: str  # Örn: "multi_stop_plan_success" veya "error"
    total_distance_km: float
    total_duration_minutes: int  # Toplam sürüş + şarj süresi
    total_co2_savings_kg: float  # Sürdürülebilirlik modülünden gelen veriler
    legs: List[Union[DriveLeg, ChargeLeg]]  # Karışık rota bacakları
