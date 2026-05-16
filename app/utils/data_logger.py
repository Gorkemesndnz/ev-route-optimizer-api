"""
Data Logger - Minimal Version
==============================

V2 ML modeli için basit training data logger.
Kaggle dataset'leri ile offline geliştirme için optimize edilmiş.

Özellikler:
- Günlük dosyalar (training_data_2025-11-27.jsonl)
- Flatten JSON (ML-ready, nested değil)
- Enterprise logger entegrasyonu

Kullanım:
    from app.utils.data_logger import log_training_data
    
    log_training_data({
        "distance_km": 450,
        "duration_min": 320,
        "consumption_kwh": 55.2
    })
"""

import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from app.utils.logger import get_logger
from app.optimization.decision_logger import anonymize_coords

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_DIR = BASE_DIR / "notebooks" / "future_training_data"

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
ENABLE_LOGGING = os.getenv("ENABLE_DATA_LOGGING", "true").lower() == "true"

logger = get_logger("DataLogger")


# =============================================================================
# HELPERS
# =============================================================================
def _get_daily_log_file() -> Path:
    """Günlük log dosyası yolu döndür"""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOG_DIR / f"training_data_{date_str}.jsonl"


def _setup_log_dir() -> None:
    """Log dizinini oluştur"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("Log dizini oluşturulamadı", error=str(e), path=str(LOG_DIR))


# =============================================================================
# PRIVACY
# =============================================================================
def _sanitize_training_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Drop direct identifiers and round coordinates before JSONL writes."""
    sanitized = {
        key: value for key, value in (data or {}).items()
        if key.lower() not in {"user_id", "ip", "email", "raw_address"}
    }
    coord_pairs = (
        ("start_lat", "start_lon"),
        ("end_lat", "end_lon"),
        ("origin_lat", "origin_lon"),
        ("destination_lat", "destination_lon"),
    )
    for lat_key, lon_key in coord_pairs:
        if lat_key in sanitized and lon_key in sanitized:
            lat, lon = anonymize_coords(sanitized.get(lat_key), sanitized.get(lon_key))
            sanitized[lat_key] = lat
            sanitized[lon_key] = lon
    return sanitized


# =============================================================================
# CORE LOGGING FUNCTION
# =============================================================================
def log_training_data(data: Dict[str, Any], category: Optional[str] = None) -> bool:
    """
    Training data'yı JSONL formatında logla (ML-ready flatten format).
    
    Args:
        data: Loglanacak veri (dict) - direkt flatten edilir
        category: Opsiyonel kategori etiketi (route, consumption, api, vb.)
    
    Returns:
        bool: Başarılı ise True
    
    Örnek:
        log_training_data({
            "distance_km": 450,
            "duration_min": 320,
            "vehicle_model": "mg4_51kwh",
            "consumption_kwh": 55.2
        }, category="route")
    
    Çıktı (flatten, ML-ready):
        {"timestamp": "...", "category": "route", "distance_km": 450, "duration_min": 320, ...}
    """
    if not ENABLE_LOGGING:
        return False
    
    try:
        # Metadata (başa ekle)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": ENVIRONMENT,
        }
        
        if category:
            record["category"] = category
        
        # Data'yı flatten olarak ekle (nested "data" key yok)
        record.update(_sanitize_training_data(data))
        
        # Günlük dosyaya yaz
        log_file = _get_daily_log_file()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        
        return True
        
    except Exception as e:
        # Enterprise logger ile uyumlu hata logu
        logger.error(
            "Training data logging başarısız",
            error=str(e),
            error_type=type(e).__name__,
            category=category,
            data_keys=list(data.keys()) if data else [],
            data_size=len(json.dumps(data, default=str)) if data else 0
        )
        return False


def log_route_decision(route_data: Dict[str, Any]) -> bool:
    """Rota kararı logla"""
    return log_training_data(route_data, category="route")


def log_consumption(consumption_data: Dict[str, Any]) -> bool:
    """Tüketim verisi logla"""
    return log_training_data(consumption_data, category="consumption")


def log_api_metric(api_data: Dict[str, Any]) -> bool:
    """API metriği logla"""
    return log_training_data(api_data, category="api")


# =============================================================================
# INITIALIZE
# =============================================================================
_setup_log_dir()
