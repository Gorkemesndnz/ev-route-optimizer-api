import logging
import sys
import structlog

# Üretim (production) için JSON tabanlı, geliştirme (development) için 
# konsol dostu loglama ayarları.

logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=logging.INFO,
)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)

# Ana JSON renderer (üretim için)
formatter = structlog.stdlib.ProcessorFormatter(
    processor=structlog.dev.ConsoleRenderer(),  # Veya üretim için: structlog.processors.JSONRenderer()
    foreign_pre_chain=[structlog.stdlib.add_log_level],
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

def get_logger(name: str):
    """Proje içinde kullanılacak ana loglama fonksiyonu"""
    return structlog.get_logger(name)
