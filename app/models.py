from enum import Enum
from typing import List, Optional, Union, Literal, Dict, Any
from pydantic import BaseModel, Field, field_validator

# ======================================================
# 1. ENUMLAR – Standartlaştırma
# ======================================================

class ChargerType(str, Enum):
    """İstasyonun genel şarj hızı tipi."""
    AC = "AC"
    DC = "DC"
    HPC = "HPC"  # High Power Charging (>=150kW)


class PlugType(str, Enum):
    """Soket tipleri."""
    TYPE2 = "Type 2"
    CCS2 = "CCS2"
    CHADEMO = "CHAdeMO"
    TESLA = "Tesla"


class WeatherCondition(str, Enum):
    """Hava durumu tipleri."""
    CLEAR = "clear"
    RAIN = "rain"
    SNOW = "snow"
    FOG = "fog"
    WINDY = "windy"
    CLOUDY = "cloudy"


class AmenityType(str, Enum):
    """Kullanıcının talep edebileceği imkan tipleri."""
    TOILET = "toilet"
    FOOD = "food"
    WIFI = "wifi"
    SHOPPING = "shopping"
    PARKING = "parking"


# ======================================================
# 2. TEMEL VERİ YAPILARI (Shared Objects)
# ======================================================

class GeoPoint(BaseModel):
    """Coğrafi koordinat modeli."""
    lat: float = Field(..., ge=-90, le=90, description="Enlem")
    lon: float = Field(..., ge=-180, le=180, description="Boylam")
    address: Optional[str] = Field(
        None,
        description="İnsan tarafından okunabilir adres (Google Geocoding sonucu)."
    )


class StationAmenity(BaseModel):
    """
    İstasyonun sunduğu imkanlar.
    🔧 V2.8: has_parking eklendi.
    """
    has_toilet: bool = False
    has_food: bool = False
    has_wifi: bool = False
    has_shopping: bool = False
    has_parking: bool = False  # 🔧 V2.8
    is_24_7: bool = False


class ConnectorInfo(BaseModel):
    """Bir soket ünitesinin bilgisi."""
    plug_type: PlugType
    charger_type: ChargerType
    power_kw: float = Field(..., gt=0, description="Maksimum güç (kW)")
    status: Literal["Available", "Occupied", "Unknown", "OutOfOrder"] = "Unknown"
    price_per_kwh: Optional[float] = Field(
        None,
        description="kWh başına ücret. Bilinmiyorsa None."
    )
    currency: str = "TRY"


class StationInfo(BaseModel):
    """
    Şarj istasyonu detayları.
    Station Finder, Route Selector ve Response tarafında ortak kullanılacak.
    
    V2.0: Google Places öncelikli + OCM fallback yapısı.
    """
    id: str
    name: str
    operator: Optional[str] = None
    location: GeoPoint
    rating: float = Field(0.0, ge=0, le=5, description="Google Maps puanı")
    user_ratings_total: Optional[int] = Field(
        None, description="Toplam kullanıcı yorum sayısı (Google)."
    )
    connectors: List[ConnectorInfo]
    amenities: StationAmenity = Field(default_factory=StationAmenity)
    distance_from_route_km: float = Field(
        0.0,
        description="Ana rotadan sapma mesafesi (km) – 0 ise direkt rota üzerindedir."
    )
    # V2.0 Google Places alanları
    data_source: Literal["google", "ocm", "unknown"] = Field(
        "unknown", description="Veri kaynağı: google veya ocm"
    )
    place_id: Optional[str] = Field(
        None, description="Google Place ID (enrichment için)"
    )
    vicinity: Optional[str] = Field(
        None, description="Yakın çevre bilgisi (Google)"
    )
    is_open_now: Optional[bool] = Field(
        None, description="Şu an açık mı (Google)"
    )


# ======================================================
# 3. ARAÇ MODELLERİ
# ======================================================

class VehicleModel(BaseModel):
    """
    vehicle_models.py içinde statik olarak tanımlanacak araç profili.
    """
    id: str  # "mg4_51kwh" gibi
    name: str  # "MG4 51 kWh"
    battery_kwh: float
    base_consumption_wh_per_km: float  # WLTP veya ortalama tüketim
    max_dc_kw: float
    max_ac_kw: float
    weight_kg: int


# ======================================================
# 4. HAVA DURUMU MODELLERİ
# ======================================================

class WeatherInfo(BaseModel):
    """
    Belirli bir nokta/zaman için hava durumu.
    V1 için yeterli, V2 ML için genişletilebilir.
    """
    temp_c: float
    condition: WeatherCondition
    wind_speed_mps: float = Field(..., ge=0)
    wind_direction_deg: int = Field(..., ge=0, le=360)
    precipitation_prob: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Yağış ihtimali (0-1 arası)."
    )


# ======================================================
# 5. ROTA TERCİHLERİ & İSTEK MODELLERİ
# ======================================================

class RoutePreferences(BaseModel):
    """
    Kullanıcının rota oluşturma tercihleri.
    İleride ML modeline de feature olarak verilebilir.
    """
    min_dest_soc: int = Field(
        10, ge=5, le=50,
        description="Varışta istenen minimum şarj yüzdesi."
    )
    min_station_soc: int = Field(
        10, ge=5, le=30,
        description="İstasyona varırken olması gereken minimum güvenlik SOC (%)."
    )
    preferred_operators: List[str] = Field(
        default_factory=list,
        description="Öncelikli istasyon markaları / operatörleri."
    )
    preferred_plug_types: List[PlugType] = Field(
        default_factory=list,
        description="Tercih edilen soket tipleri."
    )
    amenities_required: List[AmenityType] = Field(
        default_factory=list,
        description="Zorunlu istenen imkanlar (WC, yemek, wifi vs.)."
    )
    max_detour_km: float = Field(
        10.0,
        description="Bir şarj için rotadan max sapma mesafesi (km)."
    )


class RouteRequest(BaseModel):
    """
    API'ye gelen ana istek modeli (/optimize_route).
    V1'de start/end koordinat üzerinden çalışacağız.
    Üst katmanda istenirse geocoding ile string'ten GeoPoint'e çevirilebilir.
    """
    start_location: GeoPoint
    end_location: GeoPoint
    vehicle_model_id: str = Field(..., description="vehicle_models.py içindeki ID")
    current_soc_percent: float = Field(..., ge=0, le=100)
    target_arrival_soc_percent: Optional[float] = Field(
        None, ge=5, le=50,
        description="Varışta hedef batarya yüzdesi. None ise otomatik hesaplanır (10-25%)"
    )
    charge_min_soc_percent: Optional[float] = Field(
        None, ge=10, le=40,
        description="Şarj eşiği - bu %'e düşünce şarj et. None ise otomatik hesaplanır (15-25%)"
    )
    charge_target_soc_percent: Optional[float] = Field(
        None, ge=50, le=100,
        description="Şarj hedefi - istasyondan çıkış SOC. None ise otomatik hesaplanır (75-95%)"
    )
    passenger_count: Optional[int] = Field(None, ge=1, description="Yetişkin yolcu sayısı. None ise 1")
    child_count: Optional[int] = Field(None, ge=0, le=4, description="Çocuk sayısı. None ise 0")
    extra_load_kg: Optional[float] = Field(None, ge=0.0, description="Bagaj yükü (kg). None ise 0")
    departure_time_iso: Optional[str] = Field(
        None,
        description="ISO 8601 formatında çıkış zamanı. (örn: 2025-11-25T12:30:00Z)"
    )
    preferences: RoutePreferences = Field(default_factory=RoutePreferences)

    @field_validator("current_soc_percent")
    @classmethod
    def validate_soc_range(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError("current_soc_percent 0-100 arasında olmalıdır.")
        return v


# ======================================================
# 6. ROTA BACAKLARI (Drive / Charge Legs)
# ======================================================

class DriveLeg(BaseModel):
    """
    A noktasından B noktasına sürüş bacağı.
    
    start_point ve end_point hem GeoPoint hem de "lat,lon" string formatında kabul edilir.
    """
    type: Literal["drive"] = "drive"
    start_point: Union[GeoPoint, str]
    end_point: Union[GeoPoint, str]
    distance_km: float
    duration_minutes: float
    avg_speed_kmh: float = 0.0  # Opsiyonel - hesaplanabilir
    consumption_kwh: float = 0.0
    start_soc_percent: float = 0.0
    end_soc_percent: float = 0.0  # arrival_soc_percent alias
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    polyline: str = ""  # Haritada çizmek için encoded polyline string
    route_polyline: Optional[str] = None  # Backward compat alias
    weather_context: Optional[Dict[str, Any]] = None  # start_weather & end_weather
    
    # Alias for backward compatibility
    arrival_soc_percent: Optional[float] = None
    
    def model_post_init(self, __context) -> None:
        """Post-init: alias'ları senkronize et."""
        # route_polyline → polyline
        if self.route_polyline and not self.polyline:
            object.__setattr__(self, 'polyline', self.route_polyline)
        # arrival_soc_percent → end_soc_percent  
        if self.arrival_soc_percent is not None:
            object.__setattr__(self, 'end_soc_percent', self.arrival_soc_percent)


class ChargeLeg(BaseModel):
    """
    İstasyonda şarj bacağı.
    """
    type: Literal["charge"] = "charge"
    station: StationInfo
    arrival_soc_percent: float
    target_soc_percent: float
    energy_added_kwh: float
    duration_minutes: float
    price_per_kwh: Optional[float] = Field(
        None, description="Bu şarj seansında uygulanan efektif kWh fiyatı."
    )
    estimated_cost: Optional[float] = Field(
        None, description="Bu şarj için öngörülen toplam maliyet."
    )
    currency: str = "TRY"
    weather_context: Optional[WeatherInfo] = None


# ======================================================
# 7. NİHAİ ROTA CEVABI
# ======================================================

class RouteResponseStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    IMPOSSIBLE = "impossible"  # Menziil/koşullar nedeniyle rota kurulamıyorsa


class RouteResponse(BaseModel):
    """
    Kullanıcıya dönen nihai rota planı.
    """
    status: RouteResponseStatus
    message: Optional[str] = Field(
        None,
        description="Özet bilgi veya hata mesajı."
    )
    total_distance_km: float
    total_duration_minutes: float
    total_energy_kwh: float
    total_charging_cost: Optional[float] = 0.0
    total_co2_savings_kg: Optional[float] = None
    number_of_charging_stops: int
    plan_version: str = "v1.0"
    legs: List[Union[DriveLeg, ChargeLeg]]
    warning_messages: List[str] = Field(default_factory=list)


# ======================================================
# 8. MULTI-STOP RESPONSE (Basitleştirilmiş V1.3)
# ======================================================

class MultiStopRouteResponse(BaseModel):
    """
    V1.3 Çok duraklı rota planı response modeli.
    Route planner tarafından döndürülür.
    """
    status: str = Field(
        ...,
        description="Sonuç durumu: success, error_vehicle_not_found, error_api_failed, vb."
    )
    total_distance_km: float = Field(0.0, description="Toplam mesafe (km)")
    total_duration_minutes: float = Field(0.0, description="Toplam süre (dakika)")
    total_co2_savings_kg: float = Field(0.0, description="CO2 tasarrufu (kg)")
    consumption_kwh: float = Field(0.0, description="Toplam tüketim (kWh)")
    legs: List[Union[DriveLeg, ChargeLeg]] = Field(default_factory=list, description="Sürüş ve şarj bacakları")
    message: Optional[str] = Field(None, description="Ek bilgi veya hata mesajı")
    charge_stops: int = Field(0, description="Şarj durağı sayısı")
    # 🔧 V2.7: Başlangıç ve varış hava durumu
    start_weather: Optional[WeatherInfo] = Field(None, description="Başlangıç noktası hava durumu (current)")
    end_weather: Optional[WeatherInfo] = Field(None, description="Varış noktası hava durumu (forecast)")
    debug_info: Optional[dict] = Field(
        default=None, 
        description="Debug bilgileri (sadece development modunda)"
    )
