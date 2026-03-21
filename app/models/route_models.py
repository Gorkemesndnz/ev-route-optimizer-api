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


class InsightType(str, Enum):
    """Akıllı seyahat insight tipleri."""
    WARN = "warning"
    INFO = "info"
    TIP = "tip"
    SAVING = "saving"


class AmenityType(str, Enum):
    """Kullanıcının talep edebileceği imkan tipleri."""
    TOILET = "toilet"
    FOOD = "food"
    WIFI = "wifi"
    SHOPPING = "shopping"
    PARKING = "parking"
    HOTEL = "hotel"
    CAFE = "cafe"
    REST_AREA = "rest_area"
    GAS_STATION = "gas_station"


class RouteStrategy(str, Enum):
    """
    Rota seçim stratejisi.
    
    - FASTEST: En kısa süreli rota (trafik dahil)
    - EFFICIENT: En az enerji tüketen rota
    - OPTIMAL: Süre + enerji dengesi (varsayılan)
    - CHEAPEST: En düşük şarj maliyetli rota (fiyat verisi gerektirir)
    - RENEWABLE: Yenilenebilir enerji istasyonları öncelikli (veri gerektirir)
    """
    FASTEST = "fastest"
    EFFICIENT = "efficient"
    OPTIMAL = "optimal"
    CHEAPEST = "cheapest"
    RENEWABLE = "renewable"


class DrivingStyle(str, Enum):
    """
    Sürüş tarzı — tüketim çarpanı olarak kullanılır.
    
    - ECO: ×0.85 (düşük ivmelenme, max regen)
    - NORMAL: ×1.00 (referans)
    - SPORT: ×1.20 (agresif ivmelenme)
    """
    ECO = "eco"
    NORMAL = "normal"
    SPORT = "sport"

    @property
    def consumption_multiplier(self) -> float:
        return {"eco": 0.85, "normal": 1.0, "sport": 1.20}[self.value]


class ChargingFrequency(str, Enum):
    """
    Şarj sıklığı tercihi.
    
    - OPTIMAL: Dengeli durak sayısı ve şarj süresi
    - LESS: Daha az durak (düşük min SOC eşiği)
    - FREQUENT: Daha sık kısa durak (yüksek min SOC eşiği)
    """
    OPTIMAL = "optimal"
    LESS = "less"
    FREQUENT = "frequent"

    @property
    def charge_min_soc_hint(self) -> float:
        """Önerilen charge_min_soc (%) — kullanıcı override etmezse."""
        return {"optimal": 17, "less": 10, "frequent": 25}[self.value]

    @property
    def charge_target_soc_hint(self) -> float:
        """Önerilen charge_target_soc (%) — kullanıcı override etmezse."""
        return {"optimal": 80.0, "less": 85.0, "frequent": 80.0}[self.value]


class ChargerSpeedPref(str, Enum):
    """İstasyon şarj hızı tercihi."""
    HPC = "hpc"    # ≥150kW
    DC = "dc"      # ≥50kW
    AC = "ac"      # AC (yavaş)
    ANY = "any"    # Hepsi


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
    power_kw: float = Field(..., ge=0, description="Maksimum güç (kW) - Bilinmiyorsa 0.0")
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
# 3. ARAÇ MODELLERİ — MSSQL VehiclePayload
# ======================================================

class ChargeCurvePointPayload(BaseModel):
    """Şarj eğrisi noktası (.NET Gateway'den)."""
    soc: float = Field(..., ge=0, le=100)
    power_kw: float = Field(..., ge=0)


class VehiclePayload(BaseModel):
    """
    MSSQL veritabanından .NET Gateway aracılığıyla gelen araç verisi.
    Hesaplama motoru bu veriyi doğrudan kullanır.
    
    Null/0 olan alanlar için fallback mantığı resolve_vehicle_spec() fonksiyonunda.
    """
    # Kimlik
    id: int
    slug: str
    brand: str
    model: str
    variant: str = ""
    year: int = 2024
    
    # Batarya
    battery_useable_kwh: float = Field(..., gt=0, description="Kullanılabilir batarya kapasitesi (kWh)")
    battery_nominal_kwh: Optional[float] = Field(None, description="Nominal batarya kapasitesi (kWh)")
    battery_chemistry: str = Field("NMC", description="NMC / LFP / Solid State")
    battery_thermal_management: Optional[str] = Field(None, description="Sıvı Soğutmalı / Hava Soğutmalı")
    heat_pump: bool = Field(False, description="Isı pompası var mı")
    
    # WLTP verileri
    wltp_range_tel_km: Optional[int] = None
    wltp_range_teh_km: Optional[int] = None
    wltp_nominal_consumption_wh_km: Optional[float] = None
    wltp_vehicle_consumption_wh_km: Optional[float] = Field(None, description="WLTP araç tüketimi (Wh/km)")
    
    # Gerçek dünya menzil (km)
    real_range_km: Optional[int] = None
    range_cold_city_km: Optional[int] = None
    range_cold_highway_km: Optional[int] = None
    range_cold_combined_km: Optional[int] = None
    range_mild_city_km: Optional[int] = None
    range_mild_highway_km: Optional[int] = None
    range_mild_combined_km: Optional[int] = None
    
    # Gerçek dünya tüketim (Wh/km) — koşul bazlı
    efficiency_wh_km: Optional[float] = Field(None, description="Ortalama gerçek dünya tüketimi")
    efficiency_real_min_wh_km: Optional[float] = None
    efficiency_real_max_wh_km: Optional[float] = None
    efficiency_cold_city_wh_km: Optional[float] = None
    efficiency_cold_highway_wh_km: Optional[float] = None
    efficiency_cold_combined_wh_km: Optional[float] = None
    efficiency_mild_city_wh_km: Optional[float] = None
    efficiency_mild_highway_wh_km: Optional[float] = None
    efficiency_mild_combined_wh_km: Optional[float] = None
    
    # Uzun mesafe
    long_distance_rating: Optional[float] = None
    one_stop_range_cold_km: Optional[int] = None
    one_stop_range_avg_km: Optional[int] = None
    one_stop_range_mild_km: Optional[int] = None
    
    # Şarj
    fastcharge_power_max_kw: float = Field(50.0, ge=0, description="Maksimum DC şarj gücü (kW)")
    fastcharge_power_avg_kw: Optional[float] = Field(None, description="Ortalama DC şarj gücü (kW)")
    fastcharge_time_10_80_min: Optional[int] = None
    ac_charge_power_kw: float = Field(11.0, ge=0, description="AC şarj gücü (kW)")
    charging_voltage: int = Field(400, description="Şarj voltajı: 400 veya 800")
    connector_type: str = Field("CCS", description="Konnektör tipi: CCS / Type2 / CHAdeMO")
    fastcharge_port_type: Optional[str] = None
    ac_port_type: Optional[str] = None
    onboard_charger_kw: float = 11.0
    regen_max_power_kw: float = Field(70.0, ge=0, description="Maksimum regen gücü (kW)")
    battery_preconditioning: bool = False
    
    # Fiziksel ve performans
    curb_weight_kg: Optional[int] = Field(None, description="Boş araç ağırlığı (kg)")
    drag_coefficient: Optional[float] = Field(None, description="Aerodinamik sürtünme katsayısı (Cd)")
    frontal_area_m2: Optional[float] = Field(None, description="Ön kesit alanı (m²)")
    vehicle_type: str = Field("car", description="suv / sedan / hatchback / compact")
    seats: Optional[int] = 5
    top_speed_kmh: Optional[int] = None
    acceleration_0_100_sec: Optional[float] = None
    
    # Şarj eğrisi
    charge_curve: Optional[List[ChargeCurvePointPayload]] = Field(
        None,
        description="Şarj eğrisi noktaları [{soc: 10, power_kw: 150}, ...]"
    )


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

class RoadAvoidances(BaseModel):
    """Yol tercihleri — kaçınılacak yol tipleri."""
    avoid_tolls: bool = Field(False, description="Ücretli yollardan kaçın")
    avoid_highways: bool = Field(False, description="Otoyollardan kaçın")
    avoid_ferries: bool = Field(False, description="Feribotlardan kaçın")
    avoid_osmangazi_bridge: bool = Field(False, description="Osmangazi Köprüsü'nden kaçın")
    avoid_canakkale_bridge: bool = Field(False, description="1915 Çanakkale Köprüsü'nden kaçın")


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
        description="Zorunlu istenen imkanlar (WC, yemek, wifi, otel, kafe, dinlenme tesisi vs.)."
    )
    max_detour_km: float = Field(
        10.0,
        description="Bir şarj için rotadan max sapma mesafesi (km)."
    )
    # V4.0: Yol tercihleri
    road_avoidances: RoadAvoidances = Field(
        default_factory=RoadAvoidances,
        description="Kaçınılacak yol tipleri"
    )
    # V4.0: Şarj hızı tercihi
    charger_speed_pref: ChargerSpeedPref = Field(
        ChargerSpeedPref.ANY,
        description="İstasyon şarj hızı tercihi: hpc (≥150kW), dc (≥50kW), ac, any"
    )


class RouteRequest(BaseModel):
    """
    API'ye gelen ana istek modeli (/optimize_route).
    
    V4.0: vehicle_spec ile MSSQL'den zengin araç verisi,
    driving_style, charging_frequency, hvac_on, max_speed_kmh,
    consumption_override_wh_km eklendi.
    """
    start_location: GeoPoint
    end_location: GeoPoint
    waypoints: Optional[List[GeoPoint]] = Field(
        default_factory=list,
        description="Manuel eklenen ara duraklar"
    )
    vehicle_model_id: str = Field("", description="Araç ID (backward compat — vehicle_spec yoksa kullanılır)")
    current_soc_percent: float = Field(..., ge=0, le=100)
    target_arrival_soc_percent: Optional[float] = Field(
        None, ge=5, le=80,
        description="Varışta hedef batarya yüzdesi. None ise otomatik hesaplanır (10-25%)"
    )
    charge_min_soc_percent: Optional[float] = Field(
        None, ge=10, le=40,
        description="Şarj eşiği - bu %'e düşünce şarj et. None ise otomatik hesaplanır (15-25%)"
    )
    charge_target_soc_percent: Optional[float] = Field(
        None, ge=50, le=100,
        description="Şarj hedefi - istasyondan çıkış SOC. None ise otomatik hesaplanır (80-85%)"
    )
    passenger_count: Optional[int] = Field(None, ge=1, description="Yetişkin yolcu sayısı. None ise 1")
    child_count: Optional[int] = Field(None, ge=0, le=4, description="Çocuk sayısı. None ise 0")
    extra_load_kg: Optional[float] = Field(None, ge=0.0, description="Bagaj yükü (kg). None ise 0")
    departure_time_iso: Optional[str] = Field(
        None,
        description="ISO 8601 formatında çıkış zamanı. (örn: 2025-11-25T12:30:00Z). None ise 'şimdi' kabul edilir."
    )
    route_strategy: RouteStrategy = Field(
        RouteStrategy.OPTIMAL,
        description="Rota seçim stratejisi: fastest, efficient, optimal, cheapest, renewable"
    )
    preferences: RoutePreferences = Field(default_factory=RoutePreferences)
    
    # 🏠 Safe Harbor: Kullanıcı belirli bir istasyonu seçtiyse
    selected_rescue_place_id: Optional[str] = Field(
        None, 
        description="Safe Harbor durumunda kullanıcının seçtiği kurtarıcı istasyon ID'si."
    )
    
    # ====== V4.0: Yeni Alanlar ======
    
    # MSSQL'den araç verisi (.NET Gateway gönderir)
    vehicle_spec: Optional[VehiclePayload] = Field(
        None,
        description="MSSQL'den gelen araç spesifikasyonu. Varsa vehicle_model_id yerine kullanılır."
    )
    
    # Sürücü ayarları
    driving_style: DrivingStyle = Field(
        DrivingStyle.NORMAL,
        description="Sürüş tarzı: eco (×0.85), normal (×1.0), sport (×1.20)"
    )
    max_speed_kmh: Optional[int] = Field(
        None, ge=30, le=250,
        description="Gidilecek ortalama azami hız (km/h). None ise rota hızı kullanılır."
    )
    hvac_on: bool = Field(
        True,
        description="Klima açık mı? False ise sadece temel elektronik tüketimi."
    )
    consumption_override_wh_km: Optional[float] = Field(
        None, gt=0,
        description="Kullanıcının girdiği referans tüketim (Wh/km). Varsa baz tüketimi override eder."
    )
    
    # Şarj sıklığı tercihi
    charging_frequency: ChargingFrequency = Field(
        ChargingFrequency.OPTIMAL,
        description="Şarj sıklığı: optimal, less (az durak), frequent (sık durak)"
    )

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
    
    🔧 V3.1: alternative_stations eklendi - Kullanıcı feedback veya tercih değişikliği için
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
    # 🔧 V3.1: Alternatif istasyonlar (Plan B, C, D)
    alternative_stations: Optional[List[StationInfo]] = Field(
        default=None,
        description="Bu bacak için alternatif istasyonlar (en iyi 3-5). Kullanıcı feedback verirse veya istasyon değiştirmek isterse kullanılır."
    )


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

class RouteInsight(BaseModel):
    """
    🧠 V3.5: Akıllı Seyahat Asistanı insight modeli.
    Rota analizi sonucu üretilen kural-tabanlı bilgilendirme kartları.
    """
    type: InsightType = Field(..., description="Insight tipi: warning, info, tip, saving")
    title: str = Field(..., description="Kısa başlık")
    message: str = Field(..., description="Açıklama mesajı")
    icon: str = Field(..., description="Emoji ikon")
    relevance_score: float = Field(0.5, ge=0.0, le=1.0, description="Önem sırası (1.0 = en önemli)")


class MultiStopRouteResponse(BaseModel):
    """
    V3.0 Çok duraklı rota planı response modeli.
    Route planner tarafından döndürülür.
    """
    status: str = Field(
        ...,
        description="Sonuç durumu: success, error_vehicle_not_found, error_api_failed, vb."
    )
    total_distance_km: float = Field(0.0, description="Toplam mesafe (km)")
    total_duration_minutes: float = Field(0.0, description="Toplam süre (dakika) - trafik dahil")
    total_co2_savings_kg: float = Field(0.0, description="CO2 tasarrufu (kg)")
    consumption_kwh: float = Field(0.0, description="Toplam tüketim (kWh)")
    legs: List[Union[DriveLeg, ChargeLeg]] = Field(default_factory=list, description="Sürüş ve şarj bacakları")
    message: Optional[str] = Field(None, description="Ek bilgi veya hata mesajı")
    charge_stops: int = Field(0, description="Şarj durağı sayısı")
    # 🔧 V3.0: Trafik ve strateji bilgileri
    route_strategy: Optional[str] = Field(None, description="Kullanılan rota stratejisi (fastest, efficient, optimal, cheapest, renewable)")
    traffic_ratio: Optional[float] = Field(None, description="Trafik oranı (1.0 = normal, >1 = trafik var)")
    duration_without_traffic_minutes: Optional[float] = Field(None, description="Trafiksiz süre (dakika)")
    # 🔧 V2.7: Başlangıç ve varış hava durumu
    start_weather: Optional[WeatherInfo] = Field(None, description="Başlangıç noktası hava durumu (current)")
    end_weather: Optional[WeatherInfo] = Field(None, description="Varış noktası hava durumu (forecast)")
    # 🔧 V3.1: Ek metrikler
    total_regen_recovered_kwh: float = Field(0.0, description="Toplam rejeneratif frenleme ile geri kazanılan enerji (kWh)")
    total_charging_cost: float = Field(0.0, description="Toplam şarj maliyeti (TRY)")
    warning_messages: List[str] = Field(default_factory=list, description="Kullanıcı için uyarı mesajları")
    # 🧠 V3.5: Akıllı Seyahat Asistanı
    insights: List[RouteInsight] = Field(default_factory=list, description="Akıllı seyahat ipuçları ve uyarıları")
    debug_info: Optional[dict] = Field(
        default=None, 
        description="Debug bilgileri (sadece development modunda)"
    )
    # 🏠 V4.0: Safe Harbor bilgisi
    safe_harbor_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Varışta şarj istasyonu yoksa Safe Harbor bilgisi (dönüş tüketimi, kurtarıcı istasyonlar vb.)"
    )


# ======================================================
# 9. FEEDBACK & RECALCULATE MODELS (V3.1)
# ======================================================

class FeedbackType(str, Enum):
    """Kullanıcı feedback tipleri."""
    STATION_BROKEN = "station_broken"       # İstasyon arızalı
    STATION_OCCUPIED = "station_occupied"   # İstasyon meşgul
    SOC_LOW = "soc_low"                     # SOC beklenenden düşük
    HIGH_PRICE = "high_price"               # Yüksek park/şarj ücreti
    PRIVATE_PROPERTY = "private_property"   # Özel mülk, erişilemiyor
    WRONG_LOCATION = "wrong_location"       # Yanlış konum
    OTHER = "other"                         # Diğer


class StationFeedbackRequest(BaseModel):
    """
    🔧 V3.1: Kullanıcı feedback isteği.
    
    Kullanıcı mevcut rotadaki bir istasyon hakkında sorun bildirdiğinde kullanılır.
    """
    station_id: str = Field(..., description="Sorunlu istasyonun ID'si")
    feedback_type: FeedbackType = Field(..., description="Feedback tipi")
    current_location: GeoPoint = Field(..., description="Kullanıcının anlık konumu")
    current_soc_percent: float = Field(..., ge=0, le=100, description="Kullanıcının anlık SOC'u")
    destination: GeoPoint = Field(..., description="Varış noktası")
    vehicle_model_id: str = Field(..., description="Araç modeli ID")
    excluded_station_ids: List[str] = Field(
        default_factory=list,
        description="Hariç tutulacak istasyon ID'leri (daha önce sorunlu bulunanlar)"
    )
    message: Optional[str] = Field(None, description="Opsiyonel kullanıcı mesajı")


class SwitchStationRequest(BaseModel):
    """
    🔧 V3.1: İstasyon değiştirme isteği.
    
    Kullanıcı alternatif istasyonlardan birini seçtiğinde kullanılır.
    """
    original_station_id: str = Field(..., description="Orijinal seçilen istasyonun ID'si")
    new_station_id: str = Field(..., description="Yeni seçilen istasyonun ID'si")
    new_station: StationInfo = Field(..., description="Yeni istasyon bilgileri")
    leg_index: int = Field(..., ge=0, description="Değiştirilecek bacak indeksi")
    current_location: GeoPoint = Field(..., description="Kullanıcının anlık konumu")
    current_soc_percent: float = Field(..., ge=0, le=100, description="Kullanıcının anlık SOC'u")
    destination: GeoPoint = Field(..., description="Varış noktası")
    vehicle_model_id: str = Field(..., description="Araç modeli ID")
    # Sonraki bacak bilgileri (etki analizi için)
    next_station_location: Optional[GeoPoint] = Field(None, description="Sonraki istasyonun konumu")
    battery_capacity_kwh: float = Field(..., gt=0, description="Batarya kapasitesi")


class RecalculateResponse(BaseModel):
    """
    🔧 V3.1: Yeniden hesaplama yanıtı.
    """
    status: str = Field(..., description="success, partial_recalculate, full_recalculate, error")
    message: str = Field(..., description="Açıklama mesajı")
    recalculate_type: Literal["none", "single_leg", "full_route"] = Field(
        ..., description="Yeniden hesaplama tipi"
    )
    route: Optional[MultiStopRouteResponse] = Field(None, description="Yeni rota (gerekiyorsa)")
    affected_legs: List[int] = Field(default_factory=list, description="Etkilenen bacak indeksleri")
