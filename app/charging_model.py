"""
EV Charging Model v1.0
======================

Gerçekçi EV şarj eğrisi simülasyonu.

Özellikler:
- 3 fazlı şarj eğrisi (CC-CV benzeri)
- Hava durumu etkisi (sıcaklık)
- ML-ready: Tüm parametreler dışarıdan verilebilir

Bağımlılık: YOK (sadece standart kütüphaneler)

Kullanım:
    from app.charging_model import ChargingCurve, calculate_charge_time
    
    curve = ChargingCurve(battery_capacity_kwh=51.0)
    result = curve.calculate_charge_time(
        start_soc=20.0,
        target_soc=80.0,
        peak_power_kw=180.0,
        temperature_c=20.0
    )
    print(f"Şarj süresi: {result.duration_minutes} dk")
"""

from dataclasses import dataclass
from typing import Optional
from functools import lru_cache


# =============================================================================
# CONSTANTS
# =============================================================================

# Şarj eğrisi fazları
PHASE1_END_SOC = 50.0   # Peak güç sonu (Constant Current)
PHASE2_END_SOC = 80.0   # Lineer düşüş sonu
MIN_CHARGE_POWER_KW = 5.0  # Minimum şarj gücü

# Hava durumu - Modern EV'lerin kabul edilebilir aralığı
OPTIMAL_TEMP_MIN_C = 10.0   # Optimal şarj sıcaklığı alt (10°C)
OPTIMAL_TEMP_MAX_C = 40.0   # Optimal şarj sıcaklığı üst (40°C)

# Planner penaltıları
HIGH_SOC_PENALTY_FACTOR = 1.0  # %80 üzeri için dk/% penaltı


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ChargeResult:
    """Şarj hesaplama sonucu"""
    duration_minutes: float
    energy_added_kwh: float
    avg_power_kw: float
    weather_factor: float
    start_soc: float
    target_soc: float


# =============================================================================
# WEATHER IMPACT
# =============================================================================

class WeatherImpact:
    """
    Hava durumu → Şarj hızı etkisi.
    
    Soğuk havada Li-ion bataryalar yavaş şarj olur.
    Sıcak havada termal throttling olabilir.
    """
    
    @staticmethod
    def get_temperature_factor(temperature_c: Optional[float]) -> float:
        """
        Sıcaklığa göre şarj gücü çarpanı.
        
        Args:
            temperature_c: Hava sıcaklığı (°C), None ise optimal varsayılır
        
        Returns:
            0.4 - 1.0 arası çarpan (1.0 = optimal, tam güç)
        
        Örnekler:
            -20°C → 0.40 (çok yavaş)
            -10°C → 0.60
              0°C → 0.80
             10°C → 1.00 (optimal)
             40°C → 1.00 (optimal)
             50°C → 0.80 (termal throttling)
        """
        if temperature_c is None:
            return 1.0
        
        # Optimal aralık: 15-30°C → tam güç
        if OPTIMAL_TEMP_MIN_C <= temperature_c <= OPTIMAL_TEMP_MAX_C:
            return 1.0
        
        # Soğuk: Daha sert düşüş (10°C altında)
        # -20°C'de %40, 10°C'de %100
        if temperature_c < OPTIMAL_TEMP_MIN_C:
            # Her 1°C düşüşte %2 kayıp (daha sert)
            factor = 0.4 + ((temperature_c + 20) / 50.0)
            return max(0.4, min(1.0, factor))
        
        # Sıcak: Termal kısma (40°C üzerinde)
        # 40°C'de %100, 60°C'de %60
        if temperature_c > OPTIMAL_TEMP_MAX_C:
            factor = 1.0 - ((temperature_c - OPTIMAL_TEMP_MAX_C) / 50.0)
            return max(0.6, min(1.0, factor))
        
        return 1.0


# =============================================================================
# CHARGING CURVE
# =============================================================================

class ChargingCurve:
    """
    3 Fazlı EV Şarj Eğrisi Modeli
    
    Gerçek EV davranışını simüle eder:
    
    Phase 1 (0-50%):   Sabit maksimum güç (CC - Constant Current)
                       Batarya soğuk, direnç düşük → tam güç alabilir
    
    Phase 2 (50-80%):  Lineer azalan güç
                       Batarya ısınıyor, voltaj yükseliyor → güç düşürülür
    
    Phase 3 (80-100%): Hızlı düşen güç (CV - Constant Voltage)
                       Batarya dolmak üzere → çok yavaş şarj
    
    Örnek (180kW şarjcı):
        %20 → %50: ~10 dk (ortalama 180kW)
        %50 → %80: ~15 dk (ortalama 120kW)
        %80 → %100: ~30 dk (ortalama 40kW)
    """
    
    def __init__(self, battery_capacity_kwh: float = 51.0):
        """
        Args:
            battery_capacity_kwh: Araç batarya kapasitesi (kWh)
        """
        self.battery_capacity_kwh = battery_capacity_kwh
    
    def get_charge_power(
        self,
        soc: float,
        peak_power_kw: float,
        temperature_c: Optional[float] = None
    ) -> float:
        """
        Verilen SOC'da anlık şarj gücü.
        
        Args:
            soc: Mevcut batarya yüzdesi (0-100)
            peak_power_kw: Şarjcının maksimum gücü (kW)
            temperature_c: Hava sıcaklığı (°C), None ise optimal
        
        Returns:
            Anlık şarj gücü (kW)
        
        Formüller:
            Phase 1 (0-50%):  power = peak_power
            Phase 2 (50-80%): power = peak_power * (1 - 0.5 * decay)
            Phase 3 (80-100%): power = peak_power * (0.5 - 0.4 * decay)
        """
        # Sınır kontrolü
        soc = max(0.0, min(100.0, soc))
        
        # Hava durumu faktörü
        weather_factor = WeatherImpact.get_temperature_factor(temperature_c)
        
        # Fazlara göre güç hesapla
        if soc <= PHASE1_END_SOC:
            # Phase 1: Maksimum güç (CC mode)
            power = peak_power_kw
        
        elif soc <= PHASE2_END_SOC:
            # Phase 2: Lineer düşüş (50% → 80%)
            # %50'de peak, %80'de peak*0.5
            decay = (soc - PHASE1_END_SOC) / (PHASE2_END_SOC - PHASE1_END_SOC)
            power = peak_power_kw * (1.0 - 0.5 * decay)
        
        else:
            # Phase 3: Hızlı düşüş (80% → 100%) - CV mode
            # %80'de peak*0.5, %100'de ~peak*0.1
            decay = (soc - PHASE2_END_SOC) / (100.0 - PHASE2_END_SOC)
            power = peak_power_kw * (0.5 - 0.4 * decay)
        
        # Hava durumu etkisi uygula
        power *= weather_factor
        
        # Minimum güç sınırı
        return max(MIN_CHARGE_POWER_KW, power)
    
    def calculate_charge_time(
        self,
        start_soc: float,
        target_soc: float,
        peak_power_kw: float,
        temperature_c: Optional[float] = None
    ) -> ChargeResult:
        """
        Şarj süresini dakika dakika hesapla (gerçekçi eğri).
        
        Args:
            start_soc: Başlangıç SOC (%)
            target_soc: Hedef SOC (%)
            peak_power_kw: Şarjcı maksimum gücü (kW)
            temperature_c: Hava sıcaklığı (°C)
        
        Returns:
            ChargeResult: Süre, enerji, ortalama güç, hava faktörü
        
        Algoritma:
            while soc < target:
                power = get_charge_power(soc)
                energy_per_min = power / 60
                soc += (energy_per_min / battery) * 100
                time += 1
        """
        # Sınır kontrolü
        start_soc = max(0.0, min(100.0, start_soc))
        target_soc = max(0.0, min(100.0, target_soc))
        
        # Şarj gerekmiyorsa
        if target_soc <= start_soc:
            return ChargeResult(
                duration_minutes=0.0,
                energy_added_kwh=0.0,
                avg_power_kw=0.0,
                weather_factor=1.0,
                start_soc=start_soc,
                target_soc=target_soc
            )
        
        weather_factor = WeatherImpact.get_temperature_factor(temperature_c)
        
        time_minutes = 0.0
        current_soc = start_soc
        total_energy_kwh = 0.0
        
        # Dakika dakika iterasyon (daha hassas sonuç)
        while current_soc < target_soc:
            # Anlık güç (SOC ve sıcaklığa bağlı)
            power_kw = self.get_charge_power(current_soc, peak_power_kw, temperature_c)
            
            # 1 dakikada eklenen enerji
            energy_per_min = power_kw / 60.0
            
            # SOC artışı
            soc_gain = (energy_per_min / self.battery_capacity_kwh) * 100.0
            
            # Hedefi aşma kontrolü (son dakikayı orantılı hesapla)
            if current_soc + soc_gain > target_soc:
                remaining_soc = target_soc - current_soc
                fraction = remaining_soc / soc_gain if soc_gain > 0 else 0
                time_minutes += fraction
                total_energy_kwh += energy_per_min * fraction
                current_soc = target_soc
            else:
                time_minutes += 1.0
                total_energy_kwh += energy_per_min
                current_soc += soc_gain
        
        # Ortalama güç
        avg_power = (total_energy_kwh / time_minutes) * 60 if time_minutes > 0 else 0
        
        return ChargeResult(
            duration_minutes=round(time_minutes, 1),
            energy_added_kwh=round(total_energy_kwh, 2),
            avg_power_kw=round(avg_power, 1),
            weather_factor=round(weather_factor, 2),
            start_soc=start_soc,
            target_soc=target_soc
        )


# =============================================================================
# HELPER FUNCTIONS (Modüler, dışarıdan çağrılabilir)
# =============================================================================

@lru_cache(maxsize=256)
def _calculate_charge_time_cached(
    start_soc_int: int,
    target_soc_int: int,
    battery_capacity_kwh: float,
    peak_power_kw: float,
    temperature_c_int: Optional[int]
) -> tuple:
    """
    Cached version - aynı parametreler için tekrar hesaplamaz.
    LRU cache için float → int dönüşümü (hash için).
    Returns tuple for caching (dataclass is not hashable).
    """
    curve = ChargingCurve(battery_capacity_kwh)
    result = curve.calculate_charge_time(
        float(start_soc_int), 
        float(target_soc_int), 
        peak_power_kw, 
        float(temperature_c_int) if temperature_c_int is not None else None
    )
    return (
        result.duration_minutes,
        result.energy_added_kwh,
        result.avg_power_kw,
        result.weather_factor,
        result.start_soc,
        result.target_soc
    )


def calculate_charge_time(
    start_soc: float,
    target_soc: float,
    battery_capacity_kwh: float,
    peak_power_kw: float,
    temperature_c: Optional[float] = None
) -> ChargeResult:
    """
    Şarj süresi hesapla (kısayol fonksiyon) - CACHED.
    
    ChargingCurve sınıfını kullanmadan doğrudan çağrılabilir.
    Aynı parametreler için tekrar hesaplama yapmaz (LRU cache).
    
    Args:
        start_soc: Başlangıç SOC (%)
        target_soc: Hedef SOC (%)
        battery_capacity_kwh: Batarya kapasitesi (kWh)
        peak_power_kw: Şarjcı gücü (kW)
        temperature_c: Hava sıcaklığı (°C)
    
    Returns:
        ChargeResult
    
    Örnek:
        result = calculate_charge_time(20, 80, 51.0, 150.0, 20.0)
        print(f"{result.duration_minutes} dakika")
    """
    # Float → int dönüşümü (cache key için)
    start_int = int(round(start_soc))
    target_int = int(round(target_soc))
    temp_int = int(round(temperature_c)) if temperature_c is not None else None
    
    # Cached hesaplama
    cached = _calculate_charge_time_cached(
        start_int, target_int, battery_capacity_kwh, peak_power_kw, temp_int
    )
    
    # Tuple → ChargeResult dönüşümü
    return ChargeResult(
        duration_minutes=cached[0],
        energy_added_kwh=cached[1],
        avg_power_kw=cached[2],
        weather_factor=cached[3],
        start_soc=cached[4],
        target_soc=cached[5]
    )


def convert_kwh_to_soc(kwh: float, battery_capacity_kwh: float) -> float:
    """
    kWh → SOC dönüşümü.
    
    Args:
        kwh: Enerji miktarı (kWh)
        battery_capacity_kwh: Batarya kapasitesi (kWh)
    
    Returns:
        SOC yüzdesi (0-100)
    """
    if battery_capacity_kwh <= 0:
        return 0.0
    return (kwh / battery_capacity_kwh) * 100.0


def convert_soc_to_kwh(soc: float, battery_capacity_kwh: float) -> float:
    """
    SOC → kWh dönüşümü.
    
    Args:
        soc: SOC yüzdesi (0-100)
        battery_capacity_kwh: Batarya kapasitesi (kWh)
    
    Returns:
        Enerji miktarı (kWh)
    """
    return (soc / 100.0) * battery_capacity_kwh


def apply_high_soc_penalty(target_soc: float, penalty_factor: float = HIGH_SOC_PENALTY_FACTOR) -> float:
    """
    %80 üzeri şarj için planner penaltisi.
    
    %80 üzeri şarj çok yavaş olduğundan, planner bu durumu
    caydırmak için ek maliyet ekler.
    
    Args:
        target_soc: Hedef SOC (%)
        penalty_factor: Penaltı çarpanı (dk/%)
    
    Returns:
        Ek penaltı dakikası
    
    Örnek:
        target=90% → 10 dk penaltı (10% * 1.0 dk/%)
        target=80% → 0 dk penaltı
    """
    if target_soc <= PHASE2_END_SOC:
        return 0.0
    
    extra_soc = target_soc - PHASE2_END_SOC
    return extra_soc * penalty_factor


def get_charge_power_at_soc(
    soc: float,
    peak_power_kw: float,
    battery_capacity_kwh: float = 51.0,
    temperature_c: Optional[float] = None
) -> float:
    """
    Belirli SOC'da anlık şarj gücü (kısayol fonksiyon).
    
    Args:
        soc: Mevcut SOC (%)
        peak_power_kw: Şarjcı maksimum gücü (kW)
        battery_capacity_kwh: Batarya kapasitesi (kWh)
        temperature_c: Hava sıcaklığı (°C)
    
    Returns:
        Anlık güç (kW)
    """
    curve = ChargingCurve(battery_capacity_kwh)
    return curve.get_charge_power(soc, peak_power_kw, temperature_c)
