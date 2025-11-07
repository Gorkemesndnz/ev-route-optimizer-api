import json
from pathlib import Path
import datetime

# Proje ana dizininden 'notebooks/future_training_data/' klasörüne gider
BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_DIR = BASE_DIR / "notebooks/future_training_data"
LOG_FILE = LOG_DIR / "v1_training_data.jsonl"

def setup_data_logger():
    """Log klasörünün ve dosyasının var olduğundan emin olur."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if not LOG_FILE.exists():
            LOG_FILE.touch()
    except OSError as e:
        print(f"Hata: Veri log klasörü oluşturulamadı: {e}")

def log_training_data(decision_data: dict):
    """ V2 (ML) modeli eğitimi için karar verilerini JSON Lines (jsonl) formatında dosyaya loglar. """
    try:
        data_to_log = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **decision_data,
        }
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(data_to_log, ensure_ascii=False) + "\n")
    except Exception as e:
        # Ana operasyonel log'a (logger.py) hata basılmalı
        # Şimdilik print ile belirtiyoruz:
        print(f"Hata: Eğitim verisi loglanamadı: {e}")

# Modül yüklendiğinde log klasörünün hazır olmasını sağla
setup_data_logger()
