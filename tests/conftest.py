"""
conftest.py — Integration test fixtures
========================================

TestClient fixture'ı. Tüm endpoint testleri bu client'ı kullanır.
scope="module" ile her test modülünde bir kez oluşturulur.
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="module")
def client():
    """
    FastAPI TestClient — gerçek HTTP çağrıları simüle eder.
    
    scope="module": Modül başına bir kez oluşturulur.
    lifespan event'leri (startup/shutdown) otomatik tetiklenir.
    """
    with TestClient(app) as c:
        yield c


# ============================================
# Ortak Test Verileri
# ============================================

# İstanbul koordinatları (test başlangıç noktası)
ISTANBUL_LAT = 41.0082
ISTANBUL_LON = 28.9784

# Ankara koordinatları (test varış noktası)
ANKARA_LAT = 39.9334
ANKARA_LON = 32.8597

# Bilinen test araç modeli
TEST_VEHICLE_ID = "mg4_51kwh"
