"""
Response Builder
=================

Final route response oluşturma, uyarı mesajları ve ML training data loglama.

Orijinal: route_planner.py → STEP 12-13 ve response oluşturma
"""

from typing import List, Optional

from app.models import (
    MultiStopRouteResponse, RouteRequest, WeatherInfo, RouteInsight
)
from app.sustainability_calculator import calculate_co2_savings
from app.utils.logger import get_logger
from app.utils.data_logger import log_route_decision, log_consumption
from app.constants import DEFAULT_TEMPERATURE_C

logger = get_logger("route_planning.response")


def calculate_co2(
    route_distance_km: float,
    sim_result,
) -> float:
    """Gerçekçi CO2 tasarrufu hesapla (EV elektrik tüketimi dahil)."""
    try:
        ev_consumption_kwh = sim_result.total_consumption_kwh
        
        if ev_consumption_kwh <= 0:
            logger.warning("EV consumption data missing or zero, using legacy calculation")
            return calculate_co2_savings(route_distance_km, 0.0, "TR")
        
        co2_savings = calculate_co2_savings(route_distance_km, ev_consumption_kwh, "TR")
        
        logger.info(
            "Realistic CO2 savings calculated",
            distance_km=route_distance_km,
            ev_consumption_kwh=ev_consumption_kwh,
            net_co2_savings_kg=co2_savings
        )
        return co2_savings
    except Exception as e:
        logger.warning(f"CO2 calculation failed, using 0: {e}")
        return 0.0


def build_message(
    charge_stops: int,
    charge_target_soc: float,
    end_soc: float,
    can_complete_without_charging: bool,
) -> str:
    """Kullanıcı mesajı oluştur."""
    if charge_stops > 0:
        return f"{charge_stops} şarj durağı gerekli (hedef: %{round(charge_target_soc)})"
    elif can_complete_without_charging:
        return f"Şarj gerekmez. Varış SOC: %{round(end_soc)}"
    else:
        return f"Dikkat! Varış SOC: %{round(end_soc)}"


def build_warnings(
    end_soc: float,
    charge_stops: int,
    traffic_ratio: Optional[float],
    station_results: list,
    missing_station_warnings: List[str],
) -> List[str]:
    """Uyarı mesajları oluştur."""
    warnings = []

    if end_soc < 15:
        warnings.append("⚠️ Varışta düşük batarya seviyesi. Dikkatli olun.")
    if charge_stops > 3:
        warnings.append("ℹ️ Uzun rota - çoklu şarj durağı planlandı.")
    if charge_stops == 0 and end_soc < 25:
        warnings.append("💡 Şarj durağı olmadan varılabilir ama batarya düşük kalacak.")
    if traffic_ratio and traffic_ratio > 1.3:
        warnings.append("🚗 Yoğun trafik bekleniyor. Süre uzayabilir.")

    # Amenities warning'leri
    for sr in station_results:
        if sr.amenities_warning:
            warnings.append(sr.amenities_warning)
            break

    # Distance filter warning'leri (her stop için ayrı uyarı eklenir)
    for sr in station_results:
        if getattr(sr, "distance_warning", None):
            warnings.append(sr.distance_warning)

    # İstasyon bulunamayan hotspot uyarıları
    if missing_station_warnings:
        warnings.extend(missing_station_warnings)

    return warnings


def validate_plan_sanity(
    legs: list,
    charge_stops: int,
    total_distance_km: float,
    total_consumption_kwh: float,
    battery_kwh: float,
    end_soc: float,
) -> List[str]:
    """
    Post-route plan sanity check.

    Plan üretildikten sonra mantıksız sonuçları yakalar; kullanıcıya warning olarak döner.
    Engine'in sessizce kötü plan üretmesini önler.

    Returns:
        Saçma plan tespit edildiyse açıklayıcı warning string'leri (boş list = OK).
    """
    warnings: List[str] = []

    # 1) Aşırı şarj durağı sayısı:
    # Beklenen şarj sayısı = consumption / (kullanılabilir kapasite × %78)
    # %78 = optimal bant (17-95 net %78 SOC, Pareto'nun favori target'ı)
    # Threshold sıkılaştırıldı: expected * 1.25 + 0.5 (önceden 1.5 + 1 — çok yumuşaktı)
    if battery_kwh > 0 and total_consumption_kwh > 0:
        usable_per_charge = battery_kwh * 0.78
        expected_stops = max(0, total_consumption_kwh / usable_per_charge - 1)  # ilk dolu→%17 seyahat hariç
        if charge_stops > expected_stops * 1.25 + 0.5:
            warnings.append(
                f"⚠️ Plan {charge_stops} şarj durağı içeriyor; tüketime göre beklenen ~"
                f"{expected_stops:.1f}. Aşırı sık şarj — kullanıcı eşiği fazla yüksek "
                f"veya target_soc fazla düşük olabilir."
            )

    # 2) Çok yüksek SOC'ta şarj (overcharging trigger):
    for leg in legs:
        leg_type = getattr(leg, "type", None)
        if leg_type == "charge":
            arrival_soc = getattr(leg, "arrival_soc_percent", None) or getattr(leg, "soc_at_arrival", None)
            if arrival_soc is not None and arrival_soc > 35:
                warnings.append(
                    f"⚠️ Şarj durağına %{arrival_soc:.0f} batarya ile varılıyor — gereksiz erken şarj."
                )
                break  # tek warning yeter

    # 3) Çok kısa drive leg (50 km altı, başta/sonda değilse):
    drive_legs = [l for l in legs if getattr(l, "type", None) == "drive"]
    if len(drive_legs) >= 3:
        for i, leg in enumerate(drive_legs[1:-1], start=1):
            d = getattr(leg, "distance_km", 0)
            if d > 0 and d < 50:
                warnings.append(
                    f"⚠️ Şarj durakları arası mesafe çok kısa ({d:.0f} km) — plan optimal değil."
                )
                break

    # 4) Mantıksız varış SOC:
    if end_soc < 5:
        warnings.append(
            f"🚨 Varış SOC çok düşük ({end_soc:.1f}%) — plan kritik. Manuel kontrol gerekebilir."
        )
    elif end_soc > 75 and charge_stops > 0:
        warnings.append(
            f"⚠️ Varış SOC çok yüksek ({end_soc:.0f}%) — son şarj durağı gereksiz olabilir."
        )

    # 5) Fiziksel imkansız leg tespiti:
    # Bir DriveLeg start_soc düşük + distance büyük ise → SOC drop hesabı yap;
    # hesaplanan tüketim mevcut bataryadan fazlaysa istasyon bulunamamış demektir.
    for leg in legs:
        if getattr(leg, "type", None) == "drive":
            start_soc = float(getattr(leg, "start_soc_percent", 100) or 100)
            end_soc_l = float(getattr(leg, "end_soc_percent", 100) or 100)
            d = float(getattr(leg, "distance_km", 0) or 0)
            # Fiziksel: 80km/h ortalama hızda en kötü case ~350 Wh/km
            # Eğer leg start_soc < 20% AND distance > 60km → fiziksel imkansız
            if start_soc < 20 and d > 60 and end_soc_l < start_soc + 5:
                warnings.append(
                    f"🚨 Fiziksel imkansız leg: %{start_soc:.0f} SOC ile {d:.0f}km. "
                    f"Yeterli şarj istasyonu bulunamadı, rotada eksiklik var."
                )
                break

    return warnings


def validate_plan_feasibility(
    legs: list,
    missing_station_warnings: List[str],
    battery_capacity_kwh: float = 50.0,
    avg_consumption_wh_km: float = 250.0,
) -> Optional[str]:
    """
    Plan fiziksel olarak yapılabilir mi? Hard validation.

    Kontroller:
    1. Tüm hotspot'lar istasyonsuz mu (hiç şarj leg yok ama warning var)?
    2. Herhangi bir DriveLeg fiziksel imkansız mı (start_soc düşük + distance büyük)?

    Returns:
        Hata mesajı (str) → caller error_response döndürmeli.
        None → plan OK.
    """
    if not legs:
        return None

    # 1) Tüm hotspot'lar istasyonsuz mu?
    charge_legs = [l for l in legs if getattr(l, "type", None) == "charge"]
    if missing_station_warnings and not charge_legs:
        return (
            "Rotada hiçbir şarj durağı için uygun istasyon bulunamadı. "
            "Bataryanın tek seferde yetmediği bu rota tamamlanamıyor."
        )

    # 2) Fiziksel imkansız leg: start SOC × kapasite < gerekli enerji
    # Tipik 250 Wh/km tüketimle: bir leg max distance = (start_soc/100 × battery) / 0.25 km
    for leg in legs:
        if getattr(leg, "type", None) == "drive":
            start_soc = float(getattr(leg, "start_soc_percent", 100) or 100)
            d = float(getattr(leg, "distance_km", 0) or 0)
            if d <= 0 or start_soc >= 100:
                continue
            available_kwh = (start_soc / 100.0) * battery_capacity_kwh
            required_kwh = d * (avg_consumption_wh_km / 1000.0)
            # %10 buffer: gerçek tüketim kasıtla overshoot edebilir
            if required_kwh > available_kwh * 1.1:
                return (
                    f"Fiziksel imkansız bir sürüş bacağı tespit edildi: "
                    f"%{start_soc:.0f} batarya ile {d:.0f} km. "
                    f"Yeterli şarj istasyonu bulunamadı, rota planlanamıyor."
                )

    return None


def log_training_data(
    request: RouteRequest,
    battery_kwh: float,
    route_distance_km: float,
    route_duration_min: float,
    elevation_gain_m: float,
    elevation_loss_m: float,
    total_consumption: float,
    end_soc: float,
    charge_stops: int,
    co2_savings: float,
    route_result: dict,
    sim_result,
    segments_with_consumption: list,
    charge_min_soc: float,
    charge_target_soc: float,
    arrival_soc: float,
    passenger_count: int,
    child_count: int,
    extra_load_kg: float,
    avg_weather: Optional[WeatherInfo],
):
    """ML training için rota kararı ve tüketim verilerini logla."""
    log_route_decision({
        "start_lat": request.start_location.lat,
        "start_lon": request.start_location.lon,
        "end_lat": request.end_location.lat,
        "end_lon": request.end_location.lon,
        "vehicle_model": request.vehicle_model_id,
        "battery_kwh": battery_kwh,
        "initial_soc_percent": request.current_soc_percent,
        "distance_km": round(route_distance_km, 3),
        "duration_min": round(route_duration_min, 1),
        "elevation_gain_m": round(elevation_gain_m, 1),
        "elevation_loss_m": round(elevation_loss_m, 1),
        "consumption_kwh": round(total_consumption, 3),
        "arrival_soc_percent": round(end_soc, 1),
        "selection_reason": route_result.get("selection_reason"),
        "charge_stops": charge_stops,
        "co2_savings_kg": round(co2_savings, 2)
    })
    
    log_consumption({
        "vehicle_model": request.vehicle_model_id,
        "battery_kwh": battery_kwh,
        "total_segments": len(segments_with_consumption),
        "total_consumption_kwh": round(total_consumption, 3),
        "simulated_consumption_kwh": round(sim_result.total_consumption_kwh, 3),
        "start_soc_percent": request.current_soc_percent,
        "final_soc_percent": round(end_soc, 1),
        "charge_min_soc_percent": charge_min_soc,
        "target_soc_percent": charge_target_soc,
        "arrival_soc_target_percent": arrival_soc,
        "passenger_count": passenger_count,
        "child_count": child_count,
        "extra_load_kg": extra_load_kg,
        "avg_temp_c": round(avg_weather.temp_c, 1) if avg_weather else DEFAULT_TEMPERATURE_C,
        "avg_wind_speed_mps": round(avg_weather.wind_speed_mps, 1) if avg_weather else 0.0
    })


def build_route_response(
    route_distance_km: float,
    route_duration_min: float,
    co2_savings: float,
    total_consumption: float,
    legs: list,
    charge_stops: int,
    message: str,
    request: RouteRequest,
    traffic_ratio: Optional[float],
    route_leg: dict,
    start_weather: Optional[WeatherInfo],
    end_weather: Optional[WeatherInfo],
    missing_station_warnings: List[str],
    warning_messages: List[str],
    insights: List[RouteInsight] = None,
    overview_polyline: Optional[str] = None,
) -> MultiStopRouteResponse:
    """Final MultiStopRouteResponse oluştur."""
    # Trafiksiz süre — traffic_ratio = traffic_total / duration_total olduğu için
    # route_duration_min / traffic_ratio çok-leg durumlarında da total trafiksiz
    # süreyi verir. route_leg tek bir leg'in sözlüğü olduğundan multi-leg rotalarda
    # (waypoint'li) yanlış sonuç vermemek için ratio üzerinden türetiyoruz.
    if traffic_ratio and traffic_ratio > 0:
        duration_without_traffic = route_duration_min / traffic_ratio
    else:
        duration_without_traffic = route_leg["duration"]["value"] / 60
    
    # Toplam regen (gelecekte segment bazlı eklenecek)
    total_regen_kwh = 0.0
    
    # Toplam şarj maliyeti
    total_charging_cost = sum(
        leg.estimated_cost for leg in legs
        if hasattr(leg, 'estimated_cost') and leg.estimated_cost
    )
    
    return MultiStopRouteResponse(
        status="success",
        total_distance_km=round(route_distance_km, 1),
        total_duration_minutes=round(route_duration_min, 1),
        total_co2_savings_kg=round(co2_savings, 2),
        consumption_kwh=round(total_consumption, 1),
        legs=legs,
        charge_stops=charge_stops,
        message=message,
        route_strategy=request.route_strategy.value,
        traffic_ratio=round(traffic_ratio, 2) if traffic_ratio else None,
        duration_without_traffic_minutes=round(duration_without_traffic, 1),
        start_weather=start_weather,
        end_weather=end_weather,
        total_regen_recovered_kwh=round(total_regen_kwh, 2),
        total_charging_cost=round(total_charging_cost, 2),
        warning_messages=warning_messages,
        insights=insights or [],
        overview_polyline=overview_polyline or None,
    )
