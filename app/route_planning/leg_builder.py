"""
Leg Builder
=============

Multi-leg yapısı oluşturma: DriveLeg + ChargeLeg.
Şarj süresi hesaplama, istasyon bilgisi oluşturma ve alternatif istasyonlar.

Orijinal: route_planner.py → _build_multi_legs, _build_alternative_stations
"""

from typing import List, Optional, Tuple

from app.models import (
    DriveLeg, ChargeLeg, GeoPoint, StationInfo, StationAmenity,
    ConnectorInfo, PlugType, ChargerType, WeatherInfo
)
from app.charging_model import calculate_charge_time
from app.charging_tariffs import get_price_for_station
from app.consumption_engine.v2_ml_model import ChargingTimeCalculator
from app.infrastructure.vehicle_catalog import FileVehicleCatalog
from app.utils.logger import get_logger
from app.utils.charging_estimator import get_smart_dc_max, is_curve_suspicious
from app.route_planning.weather_pipeline import extract_weather_from_forecast

logger = get_logger("route_planning.leg_builder")

# Lazy-load vehicle catalog for charging curves
_vehicle_catalog = None


def _get_vehicle_catalog():
    """Lazy-load vehicle catalog (singleton pattern)."""
    global _vehicle_catalog
    if _vehicle_catalog is None:
        _vehicle_catalog = FileVehicleCatalog()
    return _vehicle_catalog


def _build_alternative_stations(
    corridor_stations: list,
    best_station_id: str,
    max_alternatives: int = 4
) -> Optional[List[StationInfo]]:
    """
    🔧 V3.1: CorridorStation listesinden alternatif istasyonları StationInfo formatına çevir.
    
    Args:
        corridor_stations: CorridorSearchResult.stations listesi
        best_station_id: Seçilen en iyi istasyonun ID'si
        max_alternatives: Maksimum alternatif sayısı
    
    Returns:
        StationInfo listesi veya None (alternatif yoksa)
    """
    if not corridor_stations:
        return None
    
    alternatives = []
    for station in corridor_stations:
        # En iyi istasyonu atla
        if station.station_id == best_station_id:
            continue
        
        if len(alternatives) >= max_alternatives:
            break
        
        # CorridorStation → StationInfo dönüşümü
        alt_amenities = StationAmenity(
            has_toilet=station.has_toilet,
            has_food=station.has_food,
            has_shopping=station.has_shopping,
            has_parking=station.has_parking,
            is_24_7=station.is_open_now is True
        )
        
        alt_station = StationInfo(
            id=station.station_id,
            name=station.station_name,
            location=station.location,
            rating=station.rating,
            connectors=[
                ConnectorInfo(
                    plug_type=PlugType.CCS2,
                    charger_type=ChargerType.DC,
                    power_kw=station.power_kw if station.power_kw > 0 else 120.0
                )
            ],
            amenities=alt_amenities,
            data_source=station.station_info.get("_source", "ocm") if hasattr(station, 'station_info') and station.station_info else "ocm",
            distance_from_route_km=station.deviation_km,
            is_open_now=station.is_open_now
        )
        alternatives.append(alt_station)
    
    return alternatives if alternatives else None


def _calculate_charge_duration(
    end_soc: float,
    hotspot_target_soc: float,
    charge_power_kw: float,
    battery_capacity_kwh: float,
    temperature_c: Optional[float],
    vehicle_model_id: Optional[str],
) -> Tuple[float, float]:
    """
    Şarj süresini hesapla (Hybrid: eğri varsa kullan, yoksa fallback).
    
    Returns:
        (charge_duration_minutes, energy_added_kwh)
    """
    catalog = _get_vehicle_catalog()
    vehicle_curve = catalog.get_charge_curve(vehicle_model_id) if vehicle_model_id else None
    vehicle_spec = catalog.get_by_id(vehicle_model_id) if vehicle_model_id else None
    
    # Eğri ve spec değerlerini al
    curve_peak_kw = 0.0
    if vehicle_curve and vehicle_curve.points:
        curve_peak_kw = max((p.power_kw for p in vehicle_curve.points), default=0.0)
    
    spec_dc_max = vehicle_spec.dc_max_kw if vehicle_spec else 0.0
    
    # Akıllı DC max: yanlış veri tespiti ve otomatik düzeltme
    dc_max, dc_source = get_smart_dc_max(
        spec_dc_max_kw=spec_dc_max,
        battery_capacity_kwh=battery_capacity_kwh,
        curve_peak_kw=curve_peak_kw,
        station_power_kw=charge_power_kw
    )
    
    kwh_to_add = (hotspot_target_soc - max(0, end_soc)) / 100.0 * battery_capacity_kwh
    
    # Eğri varsa ve güvenilirse kullan
    curve_is_valid = (
        vehicle_curve and 
        vehicle_curve.points and 
        curve_peak_kw > 1.0 and 
        not is_curve_suspicious(curve_peak_kw, battery_capacity_kwh)
    )
    
    if curve_is_valid:
        calculator = ChargingTimeCalculator(vehicle_curve, dc_max)
        charge_duration, _ = calculator.calculate_charge_time(
            battery_kwh=battery_capacity_kwh,
            soc_start=max(0, end_soc),
            soc_target=hotspot_target_soc,
            station_max_kw=charge_power_kw
        )
        logger.debug(
            f"[CHARGE] Curve mode for {vehicle_model_id}: {charge_duration:.1f} min "
            f"(station={charge_power_kw:.0f}kW, dc_max={dc_max:.0f}kW [{dc_source}])"
        )
    else:
        # Eğri yok veya güvenilir değil → akıllı fallback
        smart_power = min(dc_max, charge_power_kw)
        charge_result = calculate_charge_time(
            start_soc=max(0, end_soc),
            target_soc=hotspot_target_soc,
            battery_capacity_kwh=battery_capacity_kwh,
            peak_power_kw=smart_power,
            temperature_c=temperature_c
        )
        charge_duration = charge_result.duration_minutes
        kwh_to_add = charge_result.energy_added_kwh
        logger.debug(
            f"[CHARGE] Fallback mode for {vehicle_model_id}: {charge_duration:.1f} min "
            f"(smart_power={smart_power:.0f}kW [{dc_source}])"
        )
    
    return charge_duration, kwh_to_add


def _build_station_info(station, station_amenities=None) -> StationInfo:
    """CorridorStation → StationInfo dönüşümü."""
    station_source = station.station_info.get("_source", "ocm")
    station_place_id = station.station_info.get("_place_id")
    station_rating = station.station_info.get("_rating", station.rating)
    station_user_ratings = station.station_info.get("_user_ratings_total")
    station_vicinity = station.station_info.get("AddressInfo", {}).get("AddressLine1", "")
    
    if station_amenities is None:
        station_amenities = StationAmenity(
            has_toilet=station.has_toilet,
            has_food=station.has_food,
            has_shopping=station.has_shopping,
            has_parking=station.has_parking,
            is_24_7=station.is_open_now is True
        )
    
    return StationInfo(
        id=station.station_id,
        name=station.station_name,
        location=station.location,
        rating=station_rating,
        user_ratings_total=station_user_ratings,
        connectors=[
            ConnectorInfo(
                plug_type=PlugType.CCS2,
                charger_type=ChargerType.DC,
                power_kw=station.power_kw if station.power_kw > 0 else 120.0
            )
        ],
        amenities=station_amenities,
        data_source=station_source,
        place_id=station_place_id,
        vicinity=station_vicinity,
        distance_from_route_km=station.deviation_km,
        is_open_now=station.is_open_now
    )


def build_multi_legs(
    start_point: GeoPoint,
    end_point: GeoPoint,
    total_distance_km: float,
    total_duration_min: float,
    segments_with_consumption: List,
    start_soc: float,
    final_soc: float,
    hotspots: List,
    station_results: List,
    polyline: str,
    battery_capacity_kwh: float = 51.0,
    temperature_c: Optional[float] = None,
    weather_info: Optional[WeatherInfo] = None,
    vehicle_model_id: str = None
) -> Tuple[List, List[str]]:
    """
    Multi-leg yapısı oluştur: DriveLeg + ChargeLeg + DriveLeg + ...
    
    🔧 V2.0: SEGMENT BAZLI TÜKETİM
    Her leg için gerçek segment tüketimlerini toplar.
    
    Returns:
        (legs, missing_station_warnings)
    """
    legs = []
    total_consumption_kwh = sum(s.consumption_kwh for s in segments_with_consumption)
    
    # Şarj durağı yoksa tek DriveLeg
    if not hotspots or not station_results:
        avg_speed = (total_distance_km / total_duration_min) * 60 if total_duration_min > 0 else 60
        legs.append(DriveLeg(
            type="drive",
            start_point=start_point,
            end_point=end_point,
            distance_km=round(total_distance_km, 1),
            duration_minutes=round(total_duration_min, 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(total_consumption_kwh, 2),
            start_soc_percent=round(start_soc, 1),
            end_soc_percent=round(final_soc, 1),

            polyline=polyline,
            weather_context=weather_info.model_dump() if weather_info else None
        ))
        return legs, []
    
    # Multi-leg: Şarj durakları var
    current_point = start_point
    current_soc = start_soc
    remaining_distance = total_distance_km
    remaining_duration = total_duration_min
    missing_station_warnings = []
    
    for i, (hotspot, station_result) in enumerate(zip(hotspots, station_results)):
        if not station_result.best_station:
            warning_msg = (
                f"⚠️ {hotspot.distance_from_start_km:.0f}. km'de şarj durağı gerekiyor "
                f"(SOC: %{hotspot.soc_at_point:.0f}) ancak yakında uygun istasyon bulunamadı. "
                f"Rotanız eksik olabilir, manuel şarj planlaması önerilir."
            )
            missing_station_warnings.append(warning_msg)
            logger.warning(f"Hotspot {i+1}: No station found at {hotspot.distance_from_start_km:.0f}km (SOC: {hotspot.soc_at_point:.0f}%)")
            continue
        
        station = station_result.best_station
        station_location = station.location
        
        # Mesafe ve süre hesabı
        leg_distance = hotspot.distance_from_start_km - (total_distance_km - remaining_distance)
        leg_distance = max(0, leg_distance)
        leg_duration = (leg_distance / total_distance_km) * total_duration_min if total_distance_km > 0 else 0
        
        # Tüketim = SOC farkı × batarya kapasitesi
        end_soc = hotspot.soc_at_point
        soc_drop = current_soc - end_soc
        leg_consumption = (soc_drop / 100) * battery_capacity_kwh
        
        logger.debug(
            f"[LEG {i+1}] dist={leg_distance:.1f}km, soc={current_soc:.1f}%→{end_soc:.1f}%, "
            f"drop={soc_drop:.1f}%, cons={leg_consumption:.2f}kWh"
        )
        
        # 1. DriveLeg: Mevcut nokta → Şarj istasyonu
        avg_speed = (leg_distance / leg_duration) * 60 if leg_duration > 0 else 60
        legs.append(DriveLeg(
            type="drive",
            start_point=current_point,
            end_point=station_location,
            distance_km=round(max(0, leg_distance), 1),
            duration_minutes=round(max(0, leg_duration), 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(max(0, leg_consumption), 2),
            start_soc_percent=round(current_soc, 1),

            end_soc_percent=round(max(0, end_soc), 1),
            weather_context=weather_info.model_dump() if i == 0 and weather_info else None  # İlk bacak için start weather
        ))
        
        # 2. ChargeLeg
        hotspot_target_soc = hotspot.recommended_charge_to
        charge_power_kw = station.power_kw if station.power_kw > 0 else 120.0
        
        charge_duration, kwh_to_add = _calculate_charge_duration(
            end_soc=end_soc,
            hotspot_target_soc=hotspot_target_soc,
            charge_power_kw=charge_power_kw,
            battery_capacity_kwh=battery_capacity_kwh,
            temperature_c=temperature_c,
            vehicle_model_id=vehicle_model_id,
        )
        
        # StationInfo oluştur
        station_info = _build_station_info(station)
        
        # Forecast'ten ETA bazlı hava durumu
        elapsed_duration = total_duration_min - remaining_duration + leg_duration
        station_weather = extract_weather_from_forecast(
            station_result.weather_forecast,
            eta_minutes=elapsed_duration
        )
        charge_weather = station_weather if station_weather else weather_info
        
        # Alternatif istasyonlar
        alternative_station_infos = _build_alternative_stations(
            station_result.stations,
            best_station_id=station.station_id,
            max_alternatives=4
        )
        
        # Şarj maliyeti
        station_power = station.power_kw if station.power_kw > 0 else 120.0
        is_dc_charger = station.is_dc or station_power >= 50
        price_per_kwh, detected_operator = get_price_for_station(
            station_name=station.station_name,
            power_kw=station_power,
            is_dc=is_dc_charger
        )
        estimated_charge_cost = round(kwh_to_add * price_per_kwh, 2)
        
        legs.append(ChargeLeg(
            type="charge",
            station=station_info,
            arrival_soc_percent=round(max(0, end_soc), 1),
            target_soc_percent=round(hotspot_target_soc, 1),
            energy_added_kwh=round(kwh_to_add, 2),
            duration_minutes=round(max(10, charge_duration), 1),
            weather_context=charge_weather.model_dump() if charge_weather else None,
            alternative_stations=alternative_station_infos,
            price_per_kwh=price_per_kwh,
            estimated_cost=estimated_charge_cost
        ))
        
        # Güncellemeler
        current_point = station_location
        current_soc = hotspot_target_soc
        remaining_distance -= leg_distance
        remaining_duration -= leg_duration
    
    # Son DriveLeg: Son şarj istasyonu → Varış
    if remaining_distance > 0:
        final_soc_drop = current_soc - final_soc
        final_leg_consumption = (final_soc_drop / 100) * battery_capacity_kwh
        final_leg_distance = remaining_distance
        final_leg_duration = remaining_duration
        
        avg_speed = (final_leg_distance / final_leg_duration) * 60 if final_leg_duration > 0 else 60
        
        logger.debug(
            f"[FINAL LEG] dist={final_leg_distance:.1f}km, soc={current_soc:.1f}%→{final_soc:.1f}%, "
            f"cons={final_leg_consumption:.2f}kWh"
        )
        
        legs.append(DriveLeg(
            type="drive",
            start_point=current_point,
            end_point=end_point,
            distance_km=round(final_leg_distance, 1),
            duration_minutes=round(max(0, final_leg_duration), 1),
            avg_speed_kmh=round(avg_speed, 1),
            consumption_kwh=round(max(0, final_leg_consumption), 2),
            start_soc_percent=round(current_soc, 1),

            end_soc_percent=round(max(0, final_soc), 1),
            weather_context=charge_weather.model_dump() if 'charge_weather' in locals() and charge_weather else None
        ))
    
    logger.info(f"Multi-leg built: {len(legs)} legs (drive + charge)")
    
    return legs, missing_station_warnings
