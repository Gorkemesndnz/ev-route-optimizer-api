"""
Gelişmiş Logging Sistemi
========================

Production-ready logging sistemi:
- Renkli konsol çıktısı (development)
- JSON formatı (production)
- Hassas veri filtreleme
- Structured logging
- Thread-safe singleton pattern

Kullanım:
    from app.utils.logger import get_logger
    
    logger = get_logger(__name__)
    logger.info("API başlatıldı")
    logger.error("Hata oluştu", extra={"user_id": 123, "endpoint": "/api/route"})
"""

import logging
import sys
import os
import json
import re
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
from contextvars import ContextVar

# Windows console default cp1254 → emoji/oklara takılıyor (UnicodeEncodeError).
# Module yüklendiğinde stdout/stderr'ı UTF-8'e çevir. Python 3.7+ reconfigure() destekler.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# =============================================================================
# CONTEXT VARIABLES (Request ID tracking için)
# =============================================================================
request_id_ctx: ContextVar[Optional[str]] = ContextVar('request_id', default=None)

# =============================================================================
# AYARLAR
# =============================================================================
LOG_ROOT_DIR = os.getenv("LOG_DIR", "logs")
MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", 5 * 1024 * 1024))  # 5 MB
BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", 3))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")  # development/production/test

# Environment'a göre log seviyeleri
LOG_LEVELS = {
    "development": logging.DEBUG,
    "production": logging.INFO,
    "test": logging.WARNING,
}

CONSOLE_LEVEL = LOG_LEVELS.get(ENVIRONMENT, logging.INFO)
FILE_LEVEL = logging.DEBUG

# JSON logging (production'da aktif)
USE_JSON_FORMAT = os.getenv("LOG_FORMAT", "json" if ENVIRONMENT == "production" else "text") == "json"

# =============================================================================
# HASSAS VERİ FİLTRELEME
# =============================================================================
class SensitiveDataFilter(logging.Filter):
    """
    Hassas bilgileri (API keys, passwords, tokens) loglardan filtreler.
    
    Örnek:
        logger.info(f"API Key: {api_key}")  
        # Output: API Key: ***FILTERED***
    """
    
    # Hassas veri pattern'leri
    SENSITIVE_PATTERNS = [
        (r'password["\']?\s*[:=]\s*["\']?([^"\'\s]+)', r'password: ***FILTERED***'),
        (r'api[_-]?key["\']?\s*[:=]\s*["\']?([^"\'\s]+)', r'api_key: ***FILTERED***'),
        (r'token["\']?\s*[:=]\s*["\']?([^"\'\s]+)', r'token: ***FILTERED***'),
        (r'secret["\']?\s*[:=]\s*["\']?([^"\'\s]+)', r'secret: ***FILTERED***'),
        (r'authorization["\']?\s*[:=]\s*["\']?([^"\'\s]+)', r'authorization: ***FILTERED***'),
        (r'bearer\s+([a-zA-Z0-9\-._~+/]+=*)', r'bearer ***FILTERED***'),
        # Kredi kartı numaraları (16 haneli)
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', r'****-****-****-****'),
        # Email adresleri (kısmi gizleme)
        (r'([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', r'\1***@\2'),
    ]
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Log mesajındaki hassas verileri filtrele"""
        if isinstance(record.msg, str):
            for pattern, replacement in self.SENSITIVE_PATTERNS:
                record.msg = re.sub(pattern, replacement, record.msg, flags=re.IGNORECASE)
        
        # Args içindeki hassas verileri de temizle
        if record.args:
            cleaned_args = []
            for arg in record.args:
                if isinstance(arg, str):
                    for pattern, replacement in self.SENSITIVE_PATTERNS:
                        arg = re.sub(pattern, replacement, arg, flags=re.IGNORECASE)
                cleaned_args.append(arg)
            record.args = tuple(cleaned_args)
        
        return True


# =============================================================================
# CONTEXT FILTER (Request ID ve extra bilgiler)
# =============================================================================
class ContextFilter(logging.Filter):
    """
    Her log mesajına request_id ve environment bilgisi ekler.
    
    Distributed tracing için kritik!
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        # Request ID ekle (varsa)
        record.request_id = request_id_ctx.get() or "N/A"
        
        # Environment ekle
        record.environment = ENVIRONMENT
        
        # Thread bilgisi
        record.thread_name = record.threadName
        
        return True


# =============================================================================
# RENKLİ KONSOL FORMATTER (Senin kodundan geliştirilmiş)
# =============================================================================
class ColorFormatter(logging.Formatter):
    """
    Renkli konsol çıktısı için formatter.
    Development ortamında kullanılır.
    """
    
    COLORS = {
        "DEBUG": "\033[37m",      # White/Grey
        "INFO": "\033[36m",       # Cyan
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[41m",   # Red Background
    }
    RESET = "\033[0m"
    
    # Request ID varsa göster
    BASE_FORMAT = (
        "%(asctime)s | "
        "%(levelname)-8s | "
        "%(name)-20s | "
        "[%(request_id)s] | "
        "%(message)s"
    )
    
    def format(self, record: logging.LogRecord) -> str:
        # Renk ekle
        log_color = self.COLORS.get(record.levelname, self.RESET)
        
        # Format string'e renk ekle
        colored_format = f"{log_color}{self.BASE_FORMAT}{self.RESET}"
        
        formatter = logging.Formatter(
            colored_format,
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        return formatter.format(record)


# =============================================================================
# JSON FORMATTER (Production için)
# =============================================================================
class JSONFormatter(logging.Formatter):
    """
    JSON formatında log çıktısı.
    Production'da log aggregation için ideal (ELK, Datadog, vb.)
    
    Output örneği:
    {
        "timestamp": "2025-11-27T10:30:45.123456",
        "level": "INFO",
        "logger": "app.main",
        "message": "API started",
        "request_id": "abc-123",
        "environment": "production",
        "extra": {"user_id": 123}
    }
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, 'request_id', 'N/A'),
            "environment": getattr(record, 'environment', 'unknown'),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Exception bilgisi varsa ekle
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Extra alanları ekle (structured logging)
        if hasattr(record, 'extra_data'):
            log_data["extra"] = record.extra_data
        
        return json.dumps(log_data, ensure_ascii=False)


# =============================================================================
# LOGGER SETUP (Singleton Pattern)
# =============================================================================
_loggers: Dict[str, logging.Logger] = {}


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    console_output: bool = True,
) -> logging.Logger:
    """
    Logger oluştur ve yapılandır.
    
    Args:
        name: Logger ismi (genelde __name__ kullanılır)
        log_file: Log dosya yolu (None ise otomatik oluşturulur)
        console_output: Konsola da yazdır mı?
    
    Returns:
        Yapılandırılmış logger instance
    
    Örnek:
        logger = setup_logger(__name__)
        logger.info("Test mesajı")
    """
    
    # Zaten oluşturulmuş mu kontrol et (singleton)
    if name in _loggers:
        return _loggers[name]
    
    # Yeni logger oluştur
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # En düşük seviyeyi logger'da tut
    
    # Handler'lar varsa temizle (duplicate log engellemek için)
    if logger.handlers:
        logger.handlers.clear()
    
    # Filtreleri ekle
    sensitive_filter = SensitiveDataFilter()
    context_filter = ContextFilter()
    
    # ---------------------------------------------------------------------
    # 1. KONSOL HANDLER (Renkli veya JSON)
    # ---------------------------------------------------------------------
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(CONSOLE_LEVEL)
        
        # Environment'a göre formatter seç
        if USE_JSON_FORMAT:
            console_handler.setFormatter(JSONFormatter())
        else:
            console_handler.setFormatter(ColorFormatter())
        
        # Filtreleri ekle
        console_handler.addFilter(sensitive_filter)
        console_handler.addFilter(context_filter)
        
        logger.addHandler(console_handler)
    
    # ---------------------------------------------------------------------
    # 2. DOSYA HANDLER (Rotating, her zaman JSON)
    # ---------------------------------------------------------------------
    if log_file is None:
        # Otomatik dosya adı: logs/app_2025-11-27.log
        log_dir = Path(LOG_ROOT_DIR)
        log_dir.mkdir(exist_ok=True)
        
        log_file = log_dir / f"{name.replace('.', '_')}_{datetime.now().strftime('%Y-%m-%d')}.log"
    
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(FILE_LEVEL)
    
    # Dosyaya her zaman JSON formatında yaz (aggregation için)
    file_handler.setFormatter(JSONFormatter())
    
    # Filtreleri ekle
    file_handler.addFilter(sensitive_filter)
    file_handler.addFilter(context_filter)
    
    logger.addHandler(file_handler)
    
    # Propagation'ı kapat (root logger'a gitmesin)
    logger.propagate = False
    
    # Cache'e kaydet
    _loggers[name] = logger
    
    return logger


# =============================================================================
# STRUCTURED LOGGER WRAPPER (Keyword arguments destekli)
# =============================================================================
class StructuredLogger:
    """
    Keyword arguments destekleyen modern logger wrapper.
    
    Standart Python logger'ı sadece extra parametresi kabul eder,
    bu wrapper ile doğrudan keyword arguments kullanabilirsiniz:
    
    Örnek:
        logger.info("API çağrıldı", user_id=123, endpoint="/api/route")
        logger.error("Hata oluştu", status_code=500, detail="Internal error")
    
    Çıktı (JSON formatında):
        {
            "level": "INFO",
            "message": "API çağrıldı",
            "extra": {"user_id": 123, "endpoint": "/api/route"},
            ...
        }
    """
    
    def __init__(self, logger: logging.Logger):
        self._logger = logger
    
    def _log(self, level: int, msg: str, **kwargs) -> None:
        """Internal log method - kwargs'ı extra_data'ya dönüştürür"""
        exc_info = kwargs.pop('exc_info', None)
        stack_info = kwargs.pop('stack_info', False)
        stacklevel = kwargs.pop('stacklevel', 1)
        
        # Kalan kwargs'ları extra_data olarak ekle
        extra = {"extra_data": kwargs} if kwargs else {}
        
        self._logger.log(
            level, 
            msg, 
            exc_info=exc_info, 
            stack_info=stack_info,
            stacklevel=stacklevel + 1,  # Wrapper için +1
            extra=extra
        )
    
    def debug(self, msg: str, **kwargs) -> None:
        """DEBUG seviyesinde log"""
        self._log(logging.DEBUG, msg, **kwargs)
    
    def info(self, msg: str, **kwargs) -> None:
        """INFO seviyesinde log"""
        self._log(logging.INFO, msg, **kwargs)
    
    def warning(self, msg: str, **kwargs) -> None:
        """WARNING seviyesinde log"""
        self._log(logging.WARNING, msg, **kwargs)
    
    def error(self, msg: str, **kwargs) -> None:
        """ERROR seviyesinde log"""
        self._log(logging.ERROR, msg, **kwargs)
    
    def exception(self, msg: str, **kwargs) -> None:
        """ERROR seviyesinde log + exception traceback"""
        kwargs['exc_info'] = True
        self._log(logging.ERROR, msg, **kwargs)
    
    def critical(self, msg: str, **kwargs) -> None:
        """CRITICAL seviyesinde log"""
        self._log(logging.CRITICAL, msg, **kwargs)
    
    def log(self, level: int, msg: str, **kwargs) -> None:
        """Belirtilen seviyede log"""
        self._log(level, msg, **kwargs)
    
    # Underlying logger'a erişim için delegate
    def __getattr__(self, name: str):
        """Diğer tüm attribute'ları underlying logger'a yönlendir"""
        return getattr(self._logger, name)
    
    @property
    def handlers(self):
        """Logger handlers"""
        return self._logger.handlers
    
    @property
    def level(self):
        """Logger level"""
        return self._logger.level


# =============================================================================
# HELPER FUNCTION (Basit kullanım için)
# =============================================================================
_structured_loggers: Dict[str, StructuredLogger] = {}


def get_logger(name: str) -> StructuredLogger:
    """
    Structured logger al (keyword arguments destekli).
    
    Args:
        name: Logger ismi (genelde __name__)
    
    Returns:
        StructuredLogger instance
    
    Örnek:
        from app.utils.logger import get_logger
        
        logger = get_logger(__name__)
        logger.info("API başlatıldı", port=8000, env="production")
        logger.error("Hata oluştu", status=500, detail="DB connection failed")
    """
    if name not in _structured_loggers:
        _structured_loggers[name] = StructuredLogger(setup_logger(name))
    return _structured_loggers[name]


# =============================================================================
# REQUEST ID YÖNETİMİ
# =============================================================================
def set_request_id(request_id: str) -> None:
    """
    Mevcut context'e request ID ata.
    
    Genelde middleware'de kullanılır:
        @app.middleware("http")
        async def add_request_id(request, call_next):
            request_id = str(uuid.uuid4())
            set_request_id(request_id)
            response = await call_next(request)
            return response
    """
    request_id_ctx.set(request_id)


def get_request_id() -> Optional[str]:
    """Mevcut request ID'yi al"""
    return request_id_ctx.get()


def clear_request_id() -> None:
    """Request ID'yi temizle"""
    request_id_ctx.set(None)


# =============================================================================
# CONTEXT MANAGER (Otomatik log blokları için)
# =============================================================================
class LogBlock:
    """
    Context manager ile otomatik log blokları.
    
    Kullanım:
        with LogBlock(logger, "Rota hesaplama"):
            # ... işlemler ...
            pass
        
        # Otomatik olarak başlangıç/bitiş loglanır
    """
    
    def __init__(
        self,
        logger: logging.Logger,
        block_name: str,
        level: int = logging.INFO,
        extra: Optional[Dict[str, Any]] = None
    ):
        self.logger = logger
        self.block_name = block_name
        self.level = level
        self.extra = extra or {}
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.log(
            self.level,
            f"▶ {self.block_name} başladı",
            extra={"extra_data": self.extra}
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (datetime.now() - self.start_time).total_seconds()
        
        if exc_type is None:
            self.logger.log(
                self.level,
                f"✓ {self.block_name} tamamlandı ({duration:.2f}s)",
                extra={"extra_data": {**self.extra, "duration": duration}}
            )
        else:
            self.logger.error(
                f"✗ {self.block_name} hata ile sonlandı ({duration:.2f}s)",
                exc_info=True,
                extra={"extra_data": {**self.extra, "duration": duration}}
            )


# =============================================================================
# STRUCTURED LOGGING HELPER
# =============================================================================
def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    **context
) -> None:
    """
    Structured logging - extra context ile log.
    
    Örnek:
        log_with_context(
            logger,
            logging.INFO,
            "Kullanıcı giriş yaptı",
            user_id=123,
            ip_address="192.168.1.1",
            method="POST"
        )
    """
    logger.log(level, message, extra={"extra_data": context})


# =============================================================================
# MODÜL BAŞLATMA
# =============================================================================
# Ana uygulama logger'ı
app_logger = get_logger("ev_route_optimizer")

# İlk başlatma logu
app_logger.info(
    f"Logger başlatıldı | Environment: {ENVIRONMENT} | Format: {'JSON' if USE_JSON_FORMAT else 'TEXT'}"
)