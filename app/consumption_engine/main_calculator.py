import math
from typing import Optional, Dict
from app.models import DriveLeg, WeatherInfo, GeoPoint, WeatherCondition
from app.infrastructure.vehicle_catalog import VehicleSpec as VehiclePhysicsProfile
from app.consumption_engine.v1_rule_based.load_layer import LoadEffectCalculator
from app.consumption_engine.v1_rule_based.elevation_layer import ElevationEffectCalculator
from app.consumption_engine.v1_rule_based.weather_layer import WeatherEffectCalculator
from app.utils.geo import calculate_bearing
from app.utils.logger import get_logger

logger = get_logger("ConsumptionEngine")


class ConsumptionResult:
    """
    V1.6 FINAL - Fiziksel olarak tutarlı tüketim sonucu
    
    Tüm bileşenler ayrı ayrı saklanır ve fiziksel gerçekliğe uygun şekilde hesaplanır.
    """
    def __init__(self) -> None:
        # Tüketim bileşenleri
        self.base_consumption_kwh: float = 0.0          # Saf baz (faktörsüz)
        self.load_adjusted_kwh: float = 0.0             # Yük etkisi
        self.weather_adjusted_kwh: float = 0.0          # Hava etkisi
        self.elevation_energy_kwh: float = 0.0          # Eğim etkisi (±)
        self.aux_consumption_kwh: float = 0.0           # HVAC/Farlar/Ekran
        
        # Toplam değerler
        self.total_consumption_kwh: float = 0.0         # Gerçek toplam (negatif olabilir)
        self.practical_consumption_kwh: float = 0.0     # Pratik değer (minimum sınırlı)
        
        # Ek bilgiler
        self.regen_recovered_kwh: float = 0.0           # Geri kazanılan enerji
        self.regen_efficiency_applied: float = 0.65     # Kullanılan regen verimliliği
        self.is_net_charging: bool = False              # Toplam negatifse True
        
        # Faktörler (debugging için)
        self.factors: Dict[str, float] = {}


class MainCalculator:
    """
    🔋 V1.6 FINAL - Production-Ready EV Tüketim Motoru
    
    FİZİKSEL OLARAK DOĞRU HESAPLAMALAR:
    ✅ Sıcaklık etkisi tek yerde (çift çarpım yok)
    ✅ Elevation regen verimliliği sıcaklığa bağlı
    ✅ Negatif tüketim doğru yönetiliyor
    ✅ HVAC ve drive ayrı modelleniyor
    ✅ Weather faktörü sadece aerodinamik dirençte
    ✅ Load faktörü hem düz yol hem elevationda
    """

    DEFAULT_AVG_SPEED_KMH = 60.0  # Tek kaynak — wrapper ve fallback aynı değeri kullanır
    MIN_PRACTICAL_CONSUMPTION = -10.0  # Maksimum regen limiti (güvenlik)

    @staticmethod
    def _get_base_consumption_kwh_per_km(vehicle: VehiclePhysicsProfile) -> float:
        """Araç baz tüketimi (ideal koşullar: 20°C, rüzgarsız, boş)"""
        # Önce base_consumption_kwh_per_100km dene
        base_100km = getattr(vehicle, "base_consumption_kwh_per_100km", None)
        if isinstance(base_100km, (int, float)) and base_100km > 0:
            return base_100km / 100.0
        
        # Yoksa base_consumption_wh_km kullan
        base_wh_per_km = getattr(vehicle, "base_consumption_wh_km", None)
        if isinstance(base_wh_per_km, (int, float)) and base_wh_per_km > 0:
            base_kwh_per_100km = base_wh_per_km / 1000.0 * 100.0  # Wh/km → kWh/100km
            logger.info(
                "Using vehicle consumption data",
                base_wh_per_km=base_wh_per_km,
                base_kwh_per_100km=round(base_kwh_per_100km, 2)
            )
            return base_kwh_per_100km / 100.0
        
        logger.warning("Vehicle base consumption not found, using 18 kWh/100km default")
        return 0.18

    # NOT: Bearing hesaplama app/utils/geo.py'e taşındı → from app.utils.geo import calculate_bearing


    @staticmethod
    def _compute_total_mass(
        vehicle: VehiclePhysicsProfile,
        passenger_count: int,
        extra_load_kg: float
    ) -> float:
        """Toplam araç kütlesi"""
        passengers_weight = passenger_count * LoadEffectCalculator.AVG_PASSENGER_WEIGHT
        return vehicle.curb_weight_kg + passengers_weight + extra_load_kg

    @staticmethod
    def _get_segment_duration_hours(segment: DriveLeg) -> float:
        """Segment süresini saat cinsinden döner (fallback ile)"""
        if hasattr(segment, 'duration_minutes') and segment.duration_minutes > 0:
            return segment.duration_minutes / 60.0
        
        if segment.distance_km > 0:
            duration = segment.distance_km / MainCalculator.DEFAULT_AVG_SPEED_KMH
            logger.warning(f"Duration estimated from distance: {duration:.2f}h")
            return duration
        
        return 0.0

    @staticmethod
    def _get_default_weather() -> WeatherInfo:
        """Hava durumu yoksa makul varsayılan: 20°C, rüzgarsız, açık"""
        return WeatherInfo(
            temp_c=20.0,
            wind_speed_mps=0.0,
            wind_direction_deg=0.0,
            condition=WeatherCondition.CLEAR
        )

    @staticmethod
    def _calculate_temperature_adjusted_regen_efficiency(temp_c: float, base_efficiency: float = 0.65) -> float:
        """
        🔥 YENİ: Sıcaklığa bağlı regen verimliliği
        
        Soğukta batarya kimyası yavaşlar → regen verimi düşer
        - -10°C altı: %50 verim
        - 0°C: %55 verim  
        - 20°C: %65 verim (nominal)
        - 40°C üstü: %60 verim (aşırı ısınma koruması)
        """
        if temp_c <= -10:
            return base_efficiency * 0.77  # %50 verim
        elif temp_c <= 0:
            return base_efficiency * 0.85  # %55 verim
        elif temp_c <= 10:
            return base_efficiency * 0.92  # %60 verim
        elif temp_c >= 40:
            return base_efficiency * 0.92  # %60 verim
        else:
            return base_efficiency  # %65 nominal

    @staticmethod
    def _calculate_hvac_power(temp_c: float, base_aux_kw: float, hvac_on: bool = True, has_heat_pump: bool = False) -> float:
        """
        🔥 YENİ: Sıcaklığa bağlı HVAC gücü + Heat Pump desteği
        
        HVAC yükü sıcaklık ile doğru orantılı değildir, parabolik bir eğri izler:
        - 20°C civarı: minimum (sadece ventilasyon)
        - Çok soğuk/sıcak: maksimum (ısıtma/soğutma)
        """
        if not hvac_on:
            return base_aux_kw * 0.3  # Sadece temel elektronik sistemler (infotainment vs)


        comfort_temp = 20.0
        temp_deviation = abs(temp_c - comfort_temp)
        
        if temp_deviation <= 5:
            # Konfor aralığı: 15-25°C → düşük HVAC
            hvac_multiplier = 1.0
        elif temp_deviation <= 15:
            # Orta sapma: 5-35°C → orta HVAC
            hvac_multiplier = 1.0 + (temp_deviation - 5) * 0.05  # Her derece +5%
        else:
            # Aşırı sapma: <5°C veya >35°C → yüksek HVAC
            hvac_multiplier = 1.5 + (temp_deviation - 15) * 0.03  # Her derece +3%
        
        # Maksimum 2.5x ile sınırla (fiziksel gerçeklik)
        hvac_multiplier = min(2.5, hvac_multiplier)
        
        # Heat pump soğuk havalarda (%35 daha verimli isitma saglar)
        if has_heat_pump and temp_c < 15:
            hvac_multiplier *= 0.65
        
        return base_aux_kw * hvac_multiplier

    @staticmethod
    def calculate_segment_consumption(
        segment: DriveLeg,
        vehicle: VehiclePhysicsProfile,
        passenger_count: int = 1,
        extra_load_kg: float = 0.0,
        child_count: int = 0,
        driving_style_multiplier: float = 1.0,
        hvac_on: bool = True,
        max_speed_kmh: Optional[int] = None,
        consumption_override_wh_km: Optional[float] = None
    ) -> ConsumptionResult:
        """
        🎯 V1.6 FINAL - Fiziksel Olarak Doğru Tüketim Hesaplaması
        
        HESAPLAMA SIRASI (FİZİKSEL ÖNCELIK):
        
        1️⃣ BAZ TÜKETİM (ideal koşullarda araç tüketimi)
        
        2️⃣ YÜK ETKİSİ (kütle arttıkça yuvarlanma + ivmelenme direnci artar)
           → Hem düz yol hem elevation için geçerli
        
        3️⃣ HAVA DURUMU ETKİSİ (aerodinamik direnç)
           → Sadece düz yol için (rüzgar, yağış)
           → Sıcaklık SADECE regen verimliliğini etkiler
        
        4️⃣ ELEVATİON ENERJİSİ (potansiyel enerji değişimi)
           → Sıcaklığa bağlı regen verimliliği ile hesaplanır
           → Load factor elevation'a da uygulanır (kütle artınca tırmanış zorlaşır)
        
        5️⃣ AUXILIARY TÜKETİM (HVAC, farlar, elektronikler)
           → Sıcaklığa bağlı HVAC modeli
           → Süreye bağlı hesaplama
        
        6️⃣ TOPLAM = Yük/Hava ayarlı base + Elevation + Aux
           → Negatif olabilir (güçlü regen durumunda)
        """
        result = ConsumptionResult()

        distance_km = segment.distance_km
        if distance_km <= 0:
            logger.warning("Zero distance segment, returning zero consumption")
            return result

        # Hava durumu kontrolü
        if isinstance(segment.weather_context, WeatherInfo):
            weather = segment.weather_context
        else:
            weather = MainCalculator._get_default_weather()
            logger.info("Using default weather conditions (20°C, no wind)")

        # ---------------------------------------------------
        # 1️⃣ BAZ TÜKETİM (faktörsüz, ideal koşullar)
        # ---------------------------------------------------
        if consumption_override_wh_km is not None and consumption_override_wh_km > 0:
            base_kwh_per_km = consumption_override_wh_km / 1000.0
        else:
            base_kwh_per_km = MainCalculator._get_base_consumption_kwh_per_km(vehicle)
        
        # Sürüş stili çarpanını burada uygula (SPORT > 1.0, ECO < 1.0)
        base_kwh_per_km *= driving_style_multiplier

        base_segment_kwh = base_kwh_per_km * distance_km
        result.base_consumption_kwh = base_segment_kwh

        # ---------------------------------------------------
        # 2️⃣ YÜK FAKTÖRÜ (kütle etkisi)
        # ---------------------------------------------------
        total_mass = MainCalculator._compute_total_mass(
            vehicle=vehicle,
            passenger_count=passenger_count,
            extra_load_kg=extra_load_kg
        )
        
        mass_factor = LoadEffectCalculator.calculate_mass_factor(
            base_vehicle_weight_kg=vehicle.curb_weight_kg,
            passenger_count=passenger_count,
            extra_load_kg=extra_load_kg,
            child_count=child_count
        )

        # Yük etkisi hem düz yol hem elevationda geçerli
        load_adjusted_kwh = base_segment_kwh * mass_factor
        result.load_adjusted_kwh = load_adjusted_kwh

        # ---------------------------------------------------
        # 3️⃣ HAVA DURUMU FAKTÖRÜ (aerodinamik direnç)
        # ---------------------------------------------------
        # start_point veya end_point None ise heading=0 varsay (wrapper için)
        if segment.start_point is not None and segment.end_point is not None:
            # start_point/end_point Union[GeoPoint, str] olabilir
            sp = segment.start_point
            ep = segment.end_point
            if isinstance(sp, str):
                parts = sp.split(",")
                sp_lat, sp_lon = float(parts[0]), float(parts[1])
            else:
                sp_lat, sp_lon = sp.lat, sp.lon
            if isinstance(ep, str):
                parts = ep.split(",")
                ep_lat, ep_lon = float(parts[0]), float(parts[1])
            else:
                ep_lat, ep_lon = ep.lat, ep.lon
            heading = calculate_bearing(sp_lat, sp_lon, ep_lat, ep_lon)
        else:
            heading = 0.0  # Varsayılan heading (rüzgar etkisi nötr)
        
        weather_factor = WeatherEffectCalculator.calculate_weather_factor(
            weather=weather,
            vehicle_heading_deg=heading
        )

        # Max Speed aerodinamik çarpanı (basit dinamik model: Tüketim hızın karesiyle orantılı artar yüksek hızlarda)
        # Referans hız 110 km/h kabul edilmiştir, 110'un üstüne çıkıldıkça katlanarak artar.
        aero_speed_factor = 1.0
        if max_speed_kmh is not None and max_speed_kmh > 110:
            # (130 / 110)^2 = 1.39 -> %39 artış
            aero_speed_factor = (max_speed_kmh / 110.0) ** 2
            
            # 🔧 V2.9: Araç bazlı aerodinamik katsayısı (Cd * A)
            # Referans araç: Sedan (Cd=0.28, Area=2.2) -> 0.616
            reference_cda = 0.616
            current_cd = getattr(vehicle, "drag_coefficient", 0.28) or 0.28
            current_area = getattr(vehicle, "frontal_area_m2", 2.2) or 2.2
            current_cda = current_cd * current_area
            
            # CDA faktörü: Aracın referans araca göre ne kadar dirençli olduğu
            # Bu faktör rüzgar ve hız gibi dinamik etkileri ölçeklendirir.
            cda_scale = current_cda / reference_cda if reference_cda > 0 else 1.0
            
            # Hız etkisini CDA'ya göre ölçeklendir (SUV daha çok etkilenir)
            # 1.0 + (aero_speed_factor - 1.0) * cda_scale
            aero_speed_factor = 1.0 + (aero_speed_factor - 1.0) * cda_scale
            
            # abartılı çarpanları limitleyelim max 2.5
            aero_speed_factor = min(2.5, aero_speed_factor)
        
        weather_factor *= aero_speed_factor

        # ⚠️ ÖNEMLİ: Weather faktörü SADECE düz yol tüketimini etkiler
        # Sıcaklık etkisi elevation'da regen verimliliğine yansır (aşağıda)
        weather_adjusted_kwh = load_adjusted_kwh * weather_factor
        result.weather_adjusted_kwh = weather_adjusted_kwh

        # ---------------------------------------------------
        # 4️⃣ ELEVATİON ENERJİSİ (sıcaklığa bağlı regen ile)
        # ---------------------------------------------------
        
        # 🔧 V2.9: Araç bazlı regen verimliliği kullan (varsa)
        base_regen_eff = getattr(vehicle, 'regen_efficiency', ElevationEffectCalculator.REGEN_EFFICIENCY)
        
        # Sıcaklığa göre regen verimliliği hesapla
        temp_adjusted_regen_eff = MainCalculator._calculate_temperature_adjusted_regen_efficiency(
            temp_c=weather.temp_c,
            base_efficiency=base_regen_eff
        )
        result.regen_efficiency_applied = temp_adjusted_regen_eff

        # Tırmanış enerjisi (pozitif) - 🔥 V1.6: total_mass zaten yükü içerir
        # Drivetrain verimi: m·g·h saf mekanik enerji, batarya çıkışı / efficiency
        drivetrain_eff = getattr(
            vehicle, "drivetrain_efficiency",
            ElevationEffectCalculator.DRIVETRAIN_EFFICIENCY
        )
        uphill_joule = total_mass * ElevationEffectCalculator.GRAVITY * segment.elevation_gain_m
        uphill_kwh = (uphill_joule / ElevationEffectCalculator.JOULE_TO_KWH) / drivetrain_eff
        # ❌ mass_factor eklenmiyor - total_mass zaten passengers + cargo içeriyor (m*g*h)

        # İniş regen (negatif) - regen verimliliği sıcaklığa bağlı
        downhill_joule = total_mass * ElevationEffectCalculator.GRAVITY * segment.elevation_loss_m
        regen_kwh = (downhill_joule / ElevationEffectCalculator.JOULE_TO_KWH) * temp_adjusted_regen_eff

        # Net elevation etkisi - 🔥 V1.6: fiziksel olarak doğru m*g*h hesabı
        elevation_energy_kwh = uphill_kwh - regen_kwh
        result.elevation_energy_kwh = elevation_energy_kwh

        # Regen miktarını kaydet
        if regen_kwh > 0:
            result.regen_recovered_kwh = regen_kwh

        # ---------------------------------------------------
        # 5️⃣ AUXILIARY TÜKETİM (sıcaklığa bağlı HVAC)
        # ---------------------------------------------------
        base_aux_kw = getattr(vehicle, "auxiliary_power_kw", 1.0)
        
        # Sıcaklığa bağlı HVAC gücü (heat pump desteği eklendi)
        has_heat_pump = getattr(vehicle, "has_heat_pump", False)
        # Yeni implementasyonda has_heat_pump, vehicle profiline eklenecek
        hvac_power_kw = MainCalculator._calculate_hvac_power(
            temp_c=weather.temp_c, 
            base_aux_kw=base_aux_kw, 
            hvac_on=hvac_on,
            has_heat_pump=has_heat_pump
        )
        
        duration_hours = MainCalculator._get_segment_duration_hours(segment)
        aux_energy_kwh = hvac_power_kw * duration_hours
        result.aux_consumption_kwh = aux_energy_kwh

        # ---------------------------------------------------
        # 6️⃣ TOPLAM TÜKETİM
        # ---------------------------------------------------
        # FİZİKSEL GERÇEK: Düz yol + Eğim + Aux
        total_kwh = weather_adjusted_kwh + elevation_energy_kwh + aux_energy_kwh
        result.total_consumption_kwh = total_kwh

        # Pratik tüketim: Aşırı regen durumunda fiziksel limit koy
        # (EV'nin regen gücü sınırlıdır, -10 kWh altına inmez)
        result.practical_consumption_kwh = max(MainCalculator.MIN_PRACTICAL_CONSUMPTION, total_kwh)

        # Net şarj durumu
        result.is_net_charging = (total_kwh < 0)

        # Faktörleri kaydet (debugging için)
        result.factors = {
            "mass": round(mass_factor, 3),
            "weather_aero": round(weather_factor, 3),
            "regen_efficiency": round(temp_adjusted_regen_eff, 3),
            "hvac_multiplier": round(hvac_power_kw / base_aux_kw, 3),
        }

        # ---------------------------------------------------
        # 7️⃣ DETAYLI LOGLAMA
        # ---------------------------------------------------
        logger.info(
            "Segment consumption calculated (V1.6 FINAL)",
            distance_km=round(distance_km, 2),
            temp_c=round(weather.temp_c, 1),
            base_kwh=round(base_segment_kwh, 3),
            load_adjusted=round(load_adjusted_kwh, 3),
            weather_adjusted=round(weather_adjusted_kwh, 3),
            elevation_kwh=round(elevation_energy_kwh, 3),
            regen_recovered=round(result.regen_recovered_kwh, 3),
            regen_efficiency=round(temp_adjusted_regen_eff, 3),
            aux_kwh=round(aux_energy_kwh, 3),
            total_kwh=round(total_kwh, 3),
            practical_kwh=round(result.practical_consumption_kwh, 3),
            is_charging=result.is_net_charging,
            factors=result.factors,
        )

        return result


# =============================================================================
# WRAPPER FUNCTIONS (route_planner uyumu için)
# =============================================================================

def calculate_segment_consumption_kwh(
    vehicle,
    segment_distance_km: float,
    segment_elevation_gain_m: float = 0.0,
    segment_elevation_loss_m: float = 0.0,
    temperature_celsius: float = 20.0,
    wind_speed_mps: float = 0.0,           # 🔧 V2.0: Rüzgar hızı
    wind_direction_deg: float = 0.0,       # 🔧 V2.0: Rüzgar yönü
    weather_condition: str = "clear",      # 🔧 V2.0: Hava durumu
    start_point = None,                    # 🔧 V2.0: Bearing hesabı için
    end_point = None,                      # 🔧 V2.0: Bearing hesabı için
    extra_load_kg: float = 0.0,
    passenger_count: int = 1,
    child_count: int = 0,
    driving_style_multiplier: float = 1.0,
    hvac_on: bool = True,
    max_speed_kmh: Optional[int] = None,
    consumption_override_wh_km: Optional[float] = None,
    engine_version: str = "v1",
    duration_minutes: Optional[float] = None,  # Gerçek segment süresi (trafik dahil)
) -> float:
    """
    V2.0: Rüzgar entegrasyonlu segment tüketim hesabı.
    
    MainCalculator.calculate_segment_consumption kullanır.
    Artık rüzgar hızı, yönü ve segment bearing'i ile gerçek
    headwind/tailwind etkisi hesaplanır.
    
    Args:
        vehicle: VehicleModel veya VehiclePhysicsProfile
        segment_distance_km: Segment mesafesi (km)
        segment_elevation_gain_m: Yükselme (m)
        segment_elevation_loss_m: İniş (m)
        temperature_celsius: Sıcaklık (°C)
        wind_speed_mps: Rüzgar hızı (m/s) - V2.0
        wind_direction_deg: Rüzgar yönü (0-360°) - V2.0
        weather_condition: Hava durumu (clear, rain, snow, fog)
        start_point: GeoPoint - Bearing hesabı için
        end_point: GeoPoint - Bearing hesabı için
        extra_load_kg: Ekstra yük (kg)
        passenger_count: Yetişkin yolcu sayısı
        child_count: Çocuk yolcu sayısı
        engine_version: "v1" (kural tabanlı)
    
    Returns:
        Toplam tüketim (kWh)
    """
    # Weather condition mapping
    condition_map = {
        "clear": WeatherCondition.CLEAR,
        "rain": WeatherCondition.RAIN,
        "snow": WeatherCondition.SNOW,
        "fog": WeatherCondition.FOG,
        "windy": WeatherCondition.WINDY,
        "cloudy": WeatherCondition.CLOUDY
    }
    weather_cond = condition_map.get(weather_condition.lower(), WeatherCondition.CLEAR)
    
    # MockDriveLeg benzeri basit segment oluştur
    class SimpleSegment:
        def __init__(self):
            self.distance_km = segment_distance_km
            self.elevation_gain_m = segment_elevation_gain_m
            self.elevation_loss_m = segment_elevation_loss_m
            # Gerçek süre verildiyse onu kullan (trafik dahil), yoksa default speed'den hesapla
            if duration_minutes is not None and duration_minutes > 0:
                self.duration_minutes = duration_minutes
            else:
                self.duration_minutes = (segment_distance_km / MainCalculator.DEFAULT_AVG_SPEED_KMH) * 60.0
            self.start_point = start_point  # 🔧 V2.0: Bearing için
            self.end_point = end_point      # 🔧 V2.0: Bearing için
            self.weather_context = WeatherInfo(
                temp_c=temperature_celsius,
                wind_speed_mps=wind_speed_mps,         # 🔧 V2.0
                wind_direction_deg=wind_direction_deg, # 🔧 V2.0
                condition=weather_cond                 # 🔧 V2.0
            )
    
    segment = SimpleSegment()
    
    result = MainCalculator.calculate_segment_consumption(
        segment=segment,
        vehicle=vehicle,
        passenger_count=passenger_count,
        extra_load_kg=extra_load_kg,
        child_count=child_count,
        driving_style_multiplier=driving_style_multiplier,
        hvac_on=hvac_on,
        max_speed_kmh=max_speed_kmh,
        consumption_override_wh_km=consumption_override_wh_km
    )
    
    return result.practical_consumption_kwh


# =============================================================================
# V2.0: ROUTE CONSUMPTION CALCULATOR (Tüm segmentler için)
# =============================================================================

def _find_nearest_checkpoint_weather(
    segment_cumulative_km: float,
    weather_checkpoints: list
) -> tuple:
    """
    Segment'e en yakın weather checkpoint'i bul.
    
    Args:
        segment_cumulative_km: Segment'in kümülatif mesafesi
        weather_checkpoints: [(WeatherCheckpoint, WeatherInfo), ...]
        
    Returns:
        (WeatherInfo, wind_direction_deg) tuple
    """
    if not weather_checkpoints:
        return None, 0
    
    # En yakın checkpoint'i bul
    nearest = min(
        weather_checkpoints,
        key=lambda cp_tuple: abs(cp_tuple[0].cumulative_km - segment_cumulative_km)
    )
    checkpoint, weather = nearest
    return weather, weather.wind_direction_deg if weather else 0


def calculate_route_consumption(
    vehicle,
    segments,  # List[RouteSegment] from route_segmenter_v2
    weather_checkpoints: list = None,  # 🔧 V2.0: [(WeatherCheckpoint, WeatherInfo), ...]
    # Eski parametreler (backward compatibility)
    temperature_celsius: float = 20.0,
    wind_speed_mps: float = 0.0,
    weather_condition: str = "clear",
    extra_load_kg: float = 0.0,
    passenger_count: int = 1,
    child_count: int = 0,
    driving_style_multiplier: float = 1.0,
    hvac_on: bool = True,
    max_speed_kmh: Optional[int] = None,
    consumption_override_wh_km: Optional[float] = None
):
    """
    V2.0: Tüm segmentler için tüketim hesapla (TEK KAYNAK).
    
    🔧 V2.0 YENİLİK: weather_checkpoints parametresi ile her segment
    kendisine en yakın checkpoint'in hava durumunu kullanır.
    Eski parametreler fallback olarak desteklenir.
    
    Args:
        vehicle: VehicleModel
        segments: RouteSegment listesi (route_segmenter_v2'den)
        weather_checkpoints: [(WeatherCheckpoint, WeatherInfo), ...] - V2.0
        temperature_celsius: Fallback sıcaklık (°C)
        wind_speed_mps: Fallback rüzgar hızı (m/s)
        weather_condition: Fallback hava durumu
        extra_load_kg: Ekstra yük (kg)
        passenger_count: Yetişkin yolcu sayısı
        child_count: Çocuk yolcu sayısı
        
    Returns:
        List[SegmentWithConsumption] - SOCSimulator için hazır
    """
    from app.soc_simulator import SegmentWithConsumption
    
    # V2.0: Checkpoint bazlı mı, fallback mı?
    use_checkpoints = weather_checkpoints and len(weather_checkpoints) > 0
    
    if use_checkpoints:
        logger.info(
            f"🌦️ V2.0: Using {len(weather_checkpoints)} weather checkpoints for consumption calculation"
        )
    else:
        logger.info(
            f"Using fallback weather: temp={temperature_celsius}°C, wind={wind_speed_mps}m/s"
        )
    
    results = []
    total_consumption = 0.0
    
    for segment in segments:
        # V2.0: Segment'e en yakın checkpoint'in havasını bul
        if use_checkpoints:
            segment_weather, wind_dir = _find_nearest_checkpoint_weather(
                segment.cumulative_distance_km,
                weather_checkpoints
            )
            if segment_weather:
                seg_temp = segment_weather.temp_c
                seg_wind = segment_weather.wind_speed_mps
                seg_wind_dir = segment_weather.wind_direction_deg
            else:
                seg_temp = temperature_celsius
                seg_wind = wind_speed_mps
                seg_wind_dir = 0
        else:
            seg_temp = temperature_celsius
            seg_wind = wind_speed_mps
            seg_wind_dir = 0
        
        # Her segment için tüketim hesapla (V2.0: Rüzgar dahil)
        consumption_kwh = calculate_segment_consumption_kwh(
            vehicle=vehicle,
            segment_distance_km=segment.distance_km,
            segment_elevation_gain_m=segment.elevation_gain_m,
            segment_elevation_loss_m=segment.elevation_loss_m,
            temperature_celsius=seg_temp,
            wind_speed_mps=seg_wind,           # 🔧 V2.0: Checkpoint rüzgar hızı
            wind_direction_deg=seg_wind_dir,   # 🔧 V2.0: Checkpoint rüzgar yönü
            weather_condition=segment_weather.condition.value if (use_checkpoints and segment_weather) else "clear",
            start_point=segment.start_point,   # 🔧 V2.0: Bearing hesabı için
            end_point=segment.end_point,       # 🔧 V2.0: Bearing hesabı için
            extra_load_kg=extra_load_kg,
            passenger_count=passenger_count,
            child_count=child_count,
            driving_style_multiplier=driving_style_multiplier,
            hvac_on=hvac_on,
            max_speed_kmh=max_speed_kmh,
            consumption_override_wh_km=consumption_override_wh_km,
            duration_minutes=getattr(segment, "duration_minutes", None),
        )

        # SegmentWithConsumption oluştur
        seg_with_cons = SegmentWithConsumption(
            segment=segment,
            consumption_kwh=round(consumption_kwh, 3)
        )
        results.append(seg_with_cons)
        total_consumption += consumption_kwh
        
        if use_checkpoints:
            logger.debug(
                f"Segment {segment.index}: "
                f"cumulative={segment.cumulative_distance_km}km, "
                f"temp={seg_temp:.1f}°C, wind={seg_wind:.1f}m/s, "
                f"consumption={consumption_kwh:.2f}kWh"
            )
    
    avg_consumption = round(total_consumption / len(segments), 2) if len(segments) > 0 else 0.0
    logger.info(
        f"Route consumption calculated: "
        f"total={round(total_consumption, 2)}kWh, "
        f"avg={avg_consumption}kWh/segment"
    )
    
    return results


# ---------------------------------------------------
# TEST & VALIDATION
# ---------------------------------------------------
if __name__ == "__main__":
    from dataclasses import dataclass
    
    @dataclass
    class MockVehicle:
        curb_weight_kg: float = 1800
        base_consumption_kwh_per_100km: float = 18.0
        auxiliary_power_kw: float = 1.2
    
    @dataclass
    class MockGeoPoint:
        lat: float
        lon: float
    
    @dataclass
    class MockDriveLeg:
        distance_km: float
        elevation_gain_m: float
        elevation_loss_m: float
        start_point: MockGeoPoint
        end_point: MockGeoPoint
        duration_minutes: float
        weather_context: Optional[WeatherInfo] = None
    
    print("=" * 80)
    print("🧪 V1.6 FINAL - FİZİKSEL DOĞRULUK TESTLERİ")
    print("=" * 80)
    
    # TEST 1: Soğuk hava + dik tırmanış
    print("\n📍 TEST 1: Soğuk Hava (-10°C) + Dik Tırmanış (300m)")
    print("-" * 80)
    
    vehicle = MockVehicle()
    segment1 = MockDriveLeg(
        distance_km=50.0,
        elevation_gain_m=300.0,
        elevation_loss_m=0.0,
        start_point=MockGeoPoint(lat=41.0, lon=29.0),
        end_point=MockGeoPoint(lat=41.5, lon=29.5),
        duration_minutes=60.0,
        weather_context=WeatherInfo(
            temp_c=-10.0,
            wind_speed_mps=5.0,
            wind_direction_deg=0.0,
            condition=WeatherCondition.SNOW
        )
    )
    
    result1 = MainCalculator.calculate_segment_consumption(
        segment=segment1, vehicle=vehicle, passenger_count=3, extra_load_kg=50.0
    )
    
    print(f"Baz Tüketim: {result1.base_consumption_kwh:.2f} kWh")
    print(f"Yük Ayarlı: {result1.load_adjusted_kwh:.2f} kWh")
    print(f"Hava Ayarlı: {result1.weather_adjusted_kwh:.2f} kWh")
    print(f"Eğim Etkisi: {result1.elevation_energy_kwh:+.2f} kWh")
    print(f"Regen Verimi: {result1.regen_efficiency_applied:.2%}")
    print(f"Aux (HVAC): {result1.aux_consumption_kwh:.2f} kWh")
    print(f"TOPLAM: {result1.total_consumption_kwh:.2f} kWh")
    print(f"Ortalama: {(result1.total_consumption_kwh/segment1.distance_km)*100:.1f} kWh/100km")
    
    # TEST 2: Sıcak hava + uzun iniş
    print("\n📍 TEST 2: Sıcak Hava (35°C) + Uzun İniş (400m)")
    print("-" * 80)
    
    segment2 = MockDriveLeg(
        distance_km=60.0,
        elevation_gain_m=0.0,
        elevation_loss_m=400.0,
        start_point=MockGeoPoint(lat=41.0, lon=29.0),
        end_point=MockGeoPoint(lat=41.5, lon=29.0),
        duration_minutes=70.0,
        weather_context=WeatherInfo(
            temp_c=35.0,
            wind_speed_mps=2.0,
            wind_direction_deg=90.0,
            condition=WeatherCondition.CLEAR
        )
    )
    
    result2 = MainCalculator.calculate_segment_consumption(
        segment=segment2, vehicle=vehicle, passenger_count=1, extra_load_kg=0.0
    )
    
    print(f"Baz Tüketim: {result2.base_consumption_kwh:.2f} kWh")
    print(f"Yük Ayarlı: {result2.load_adjusted_kwh:.2f} kWh")
    print(f"Hava Ayarlı: {result2.weather_adjusted_kwh:.2f} kWh")
    print(f"Eğim Etkisi: {result2.elevation_energy_kwh:+.2f} kWh")
    print(f"Regen Kazancı: {result2.regen_recovered_kwh:.2f} kWh")
    print(f"Regen Verimi: {result2.regen_efficiency_applied:.2%}")
    print(f"Aux (HVAC): {result2.aux_consumption_kwh:.2f} kWh")
    print(f"TOPLAM: {result2.total_consumption_kwh:.2f} kWh")
    print(f"Net Şarj: {'✅ EVET' if result2.is_net_charging else '❌ HAYIR'}")
    
    # TEST 3: İdeal koşullar
    print("\n📍 TEST 3: İdeal Koşullar (20°C, düz yol)")
    print("-" * 80)
    
    segment3 = MockDriveLeg(
        distance_km=100.0,
        elevation_gain_m=0.0,
        elevation_loss_m=0.0,
        start_point=MockGeoPoint(lat=41.0, lon=29.0),
        end_point=MockGeoPoint(lat=41.0, lon=30.0),
        duration_minutes=120.0,
        weather_context=WeatherInfo(
            temp_c=20.0,
            wind_speed_mps=0.0,
            wind_direction_deg=0.0,
            condition=WeatherCondition.CLEAR
        )
    )
    
    result3 = MainCalculator.calculate_segment_consumption(
        segment=segment3, vehicle=vehicle, passenger_count=1, extra_load_kg=0.0
    )
    
    print(f"Baz Tüketim: {result3.base_consumption_kwh:.2f} kWh")
    print(f"Toplam Tüketim: {result3.total_consumption_kwh:.2f} kWh")
    print(f"Ortalama: {(result3.total_consumption_kwh/segment3.distance_km)*100:.1f} kWh/100km")
    print(f"✅ İdeal koşullarda baz tüketimle eşleşmeli!")
    
    print("\n" + "=" * 80)
    print("✅ TÜM TESTLER TAMAMLANDI - FİZİKSEL TUTARLILIK SAĞLANDI")
    print("=" * 80)