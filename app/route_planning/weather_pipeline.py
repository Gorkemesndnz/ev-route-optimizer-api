"""
Weather Pipeline
=================

Hava durumu pipeline: forecast çözümleme, checkpoint oluşturma ve 
Pass 2 weather refinement.

Orijinal: route_planner.py → _extract_weather_from_forecast, STEP 6, STEP 10.5
"""

import asyncio
import time
from typing import Optional, List, Dict, Any, Tuple

from app.models import WeatherInfo, WeatherCondition
from app.route_segmenter import RouteSegmenter, WeatherCheckpoint
from app.services.weather_service import WeatherService
from app.utils.logger import get_logger
from app.constants import DEFAULT_TEMPERATURE_C

logger = get_logger("route_planning.weather")


def extract_weather_from_forecast(
    forecast_data: Optional[Dict[str, Any]],
    eta_minutes: float = 0.0
) -> Optional[WeatherInfo]:
    """
    🔧 V2.7: Forecast verisinden ETA'ya en yakın hava durumunu çıkar.
    
    OpenWeatherMap forecast 3 saatlik dilimler verir.
    ETA'ya en yakın dilimi seçerek gerçek varış anı havasını döndürür.
    
    Args:
        forecast_data: OpenWeatherMap forecast API yanıtı
        eta_minutes: Tahmini varış süresi (dakika)
    
    Returns:
        WeatherInfo veya None
    """
    if not forecast_data or not isinstance(forecast_data, dict):
        return None
    
    forecast_list = forecast_data.get("list", [])
    if not forecast_list:
        return None
    
    # ETA timestamp hesapla
    current_time = time.time()
    eta_timestamp = current_time + (eta_minutes * 60)
    
    # En yakın forecast dilimini bul
    closest_forecast = None
    min_diff = float('inf')
    
    for item in forecast_list:
        dt = item.get("dt", 0)
        diff = abs(dt - eta_timestamp)
        if diff < min_diff:
            min_diff = diff
            closest_forecast = item
    
    if not closest_forecast:
        return None
    
    # WeatherInfo oluştur
    try:
        main = closest_forecast.get("main", {})
        wind = closest_forecast.get("wind", {})
        weather_list = closest_forecast.get("weather", [])
        
        temp_c = float(main.get("temp", 20.0))
        wind_mps = float(wind.get("speed", 0.0))
        wind_deg = int(wind.get("deg", 0))
        pop = float(closest_forecast.get("pop", 0.0))  # Yağış olasılığı
        
        # Condition mapping
        condition = WeatherCondition.CLOUDY
        if weather_list:
            condition_id = weather_list[0].get("id", 800)
            if condition_id == 800:
                condition = WeatherCondition.CLEAR
            elif 801 <= condition_id <= 804:
                condition = WeatherCondition.CLOUDY
            elif 500 <= condition_id < 600:
                condition = WeatherCondition.RAIN
            elif 600 <= condition_id < 700:
                condition = WeatherCondition.SNOW
            elif 700 <= condition_id < 800:
                condition = WeatherCondition.FOG
        
        return WeatherInfo(
            temp_c=temp_c,
            condition=condition,
            wind_speed_mps=wind_mps,
            wind_direction_deg=wind_deg,
            precipitation_prob=pop
        )
    except Exception as e:
        logger.warning(f"Failed to parse forecast: {e}")
        return None


def _default_weather() -> WeatherInfo:
    """Fallback hava durumu: varsayılan sıcaklık, rüzgarsız, açık."""
    return WeatherInfo(
        temp_c=DEFAULT_TEMPERATURE_C,
        condition=WeatherCondition.CLEAR,
        wind_speed_mps=0.0,
        wind_direction_deg=0,
        precipitation_prob=0.0
    )


async def fetch_weather_checkpoints(
    segmenter: RouteSegmenter,
    route_duration_min: float,
    weather_service: WeatherService,
    checkpoint_interval_km: float = 100.0
) -> List[Tuple[WeatherCheckpoint, WeatherInfo]]:
    """
    🌦️ V2.0: Her checkpoint_interval_km'de bir hava durumu verisi çek.
    
    Args:
        segmenter: RouteSegmenter instance (create_weather_checkpoints için)
        route_duration_min: Toplam rota süresi (dakika)
        weather_service: WeatherService instance
        checkpoint_interval_km: Checkpoint aralığı (km)
    
    Returns:
        [(WeatherCheckpoint, WeatherInfo), ...] listesi
    """
    checkpoint_weather: List[Tuple[WeatherCheckpoint, WeatherInfo]] = []
    
    try:
        # Checkpoint'leri oluştur
        weather_checkpoints = segmenter.create_weather_checkpoints(
            total_duration_minutes=route_duration_min,
            checkpoint_interval_km=checkpoint_interval_km
        )
        logger.info(f"Weather checkpoints created: {len(weather_checkpoints)} points")
        
        if not weather_checkpoints:
            return checkpoint_weather
        
        # Paralel forecast çağrısı
        forecast_tasks = [
            weather_service.get_forecast_for_point(cp.lat, cp.lon)
            for cp in weather_checkpoints
        ]
        forecasts = await asyncio.gather(*forecast_tasks, return_exceptions=True)
        
        # Her checkpoint için ETA-matched weather çıkar
        for cp, forecast in zip(weather_checkpoints, forecasts):
            if isinstance(forecast, Exception):
                logger.warning(f"Forecast failed for checkpoint {cp.cumulative_km}km: {forecast}")
                weather = None
            else:
                weather = extract_weather_from_forecast(forecast, eta_minutes=cp.eta_minutes)
            
            # Fallback: varsayılan hava durumu
            if not weather:
                weather = _default_weather()
            
            checkpoint_weather.append((cp, weather))
        
        logger.info(f"Weather data fetched for {len(checkpoint_weather)} checkpoints")
    except Exception as e:
        logger.warning(f"Weather checkpoint system failed: {e}, using defaults")
    
    return checkpoint_weather


async def fetch_start_end_weather(
    start_coords: Dict[str, float],
    end_coords: Dict[str, float],
    route_duration_min: float,
    weather_service: WeatherService,
) -> Tuple[Optional[WeatherInfo], Optional[WeatherInfo], Optional[WeatherInfo]]:
    """
    Başlangıç ve varış noktalarının hava durumunu çek.
    
    Returns:
        (start_weather, end_weather, avg_weather)
    """
    start_weather: Optional[WeatherInfo] = None
    end_weather: Optional[WeatherInfo] = None
    avg_weather: Optional[WeatherInfo] = None
    
    try:
        start_weather = await weather_service.get_weather_at_point(
            start_coords["lat"], start_coords["lng"]
        )
        if start_weather:
            logger.info(f"Start weather: {start_weather.temp_c}°C, {start_weather.condition.value}")
    except Exception as e:
        logger.warning(f"Start weather fetch failed: {e}")
    
    try:
        end_forecast = await weather_service.get_forecast_for_point(
            end_coords["lat"], end_coords["lng"]
        )
        end_weather = extract_weather_from_forecast(end_forecast, eta_minutes=route_duration_min)
        if end_weather:
            logger.info(f"End weather (forecast): {end_weather.temp_c}°C, {end_weather.condition.value}")
    except Exception as e:
        logger.warning(f"End weather fetch failed: {e}")
    
    # Ortalama hava durumu hesapla
    if start_weather and end_weather:
        avg_weather = WeatherInfo(
            temp_c=(start_weather.temp_c + end_weather.temp_c) / 2,
            condition=start_weather.condition,
            wind_speed_mps=(start_weather.wind_speed_mps + end_weather.wind_speed_mps) / 2,
            wind_direction_deg=start_weather.wind_direction_deg,
            precipitation_prob=max(
                start_weather.precipitation_prob or 0,
                end_weather.precipitation_prob or 0
            )
        )
    elif start_weather:
        avg_weather = start_weather
    elif end_weather:
        avg_weather = end_weather
    
    return start_weather, end_weather, avg_weather


async def refine_weather_pass2(
    hotspots,
    station_results,
    start_weather: Optional[WeatherInfo],
    end_weather: Optional[WeatherInfo],
    avg_weather: Optional[WeatherInfo],
    route_distance_km: float,
    weather_service: WeatherService,
) -> Optional[WeatherInfo]:
    """
    🔧 STEP 10.5: Hotspot lokasyonlarındaki hava durumu ile refined ortalama hesapla.
    
    Args:
        hotspots: ChargeHotspot listesi
        station_results: İstasyon arama sonuçları
        start_weather: Başlangıç hava durumu
        end_weather: Varış hava durumu
        avg_weather: Mevcut ortalama hava durumu
        route_distance_km: Toplam rota mesafesi
        weather_service: WeatherService instance
    
    Returns:
        Refined WeatherInfo veya None (refinement yapılamadıysa)
    """
    weather_points = []
    weather_weights = []  # Mesafe bazlı ağırlıklar
    
    # Start noktası
    if start_weather:
        weather_points.append(start_weather)
        weather_weights.append(hotspots[0].distance_from_start_km if hotspots else route_distance_km / 2)
    
    # Her hotspot için hava durumu
    prev_km = 0.0
    for i, (hotspot, station_result) in enumerate(zip(hotspots, station_results)):
        if station_result.best_station:
            station = station_result.best_station
            hotspot_weather = await weather_service.get_weather_at_point(
                station.location.lat, station.location.lon
            )
            if hotspot_weather:
                weather_points.append(hotspot_weather)
                leg_distance = hotspot.distance_from_start_km - prev_km
                weather_weights.append(leg_distance)
                prev_km = hotspot.distance_from_start_km
                logger.debug(f"Hotspot {i+1} weather: {hotspot_weather.temp_c}°C at {station.station_name}")
    
    # End noktası
    if end_weather:
        weather_points.append(end_weather)
        remaining_distance = route_distance_km - prev_km
        weather_weights.append(remaining_distance)
    
    # Ağırlıklı ortalama hesapla (en az 2 nokta varsa)
    if len(weather_points) >= 2 and sum(weather_weights) > 0:
        total_weight = sum(weather_weights)
        refined_temp = sum(w.temp_c * wt for w, wt in zip(weather_points, weather_weights)) / total_weight
        refined_wind = sum(w.wind_speed_mps * wt for w, wt in zip(weather_points, weather_weights)) / total_weight
        
        refined_weather = WeatherInfo(
            temp_c=refined_temp,
            condition=weather_points[0].condition,
            wind_speed_mps=refined_wind,
            wind_direction_deg=0,
            precipitation_prob=0.0
        )
        
        old_temp = avg_weather.temp_c if avg_weather else DEFAULT_TEMPERATURE_C
        temp_diff = abs(refined_temp - old_temp)
        
        logger.info(
            f"Pass 2 weather: {len(weather_points)} points, "
            f"refined_temp={refined_temp:.1f}°C (was {old_temp:.1f}°C, diff={temp_diff:.1f}°C)"
        )
        
        return refined_weather
    
    return None
