# EV Route Optimizer API v1.3

Akıllı Elektrikli Araç Rota Optimizasyonu Servisi

Enterprise V1.3 mimarisi ile geliştirilmiş, çok duraklı rota planlaması, şarj istasyonu optimizasyonu ve CO2 tasarrufu hesaplaması sunan REST API.

## ✨ Özellikler

- **🗺️ Multi-Stop Route Optimization** - Birden fazla durak destekli rota planlaması
- **⚡ Smart Charging Planning** - OCM ve Google Maps entegrasyonlu şarj istasyonu bulma
- **📊 Real-time Consumption** - Elevation, hava durumu ve araç özelliklerine göre tüketim hesabı
- **🌱 CO2 Savings** - Benzinli araçlara karşı çevresel tasarruf analizi
- **🔍 Station Funnel Algorithm** - 6 adımlı istasyon seçim ve skorlama algoritması
- **📝 Structured Logging** - Detaylı hata takibi ve debug bilgisi
- **🚀 Enterprise Ready** - CORS, rate limiting, caching ve error handling

## 🏗️ Teknoloji Stack

- **Framework**: FastAPI (Python 3.8+)
- **API Integration**: Google Maps, Open Charge Map, OpenWeatherMap
- **Data Validation**: Pydantic Models
- **Logging**: Structured Logging
- **HTTP Client**: httpx (async)
- **Server**: Uvicorn

## 🚀 Kurulum

### 1. Repository Clone
```bash
git clone <repository-url>
cd Ev-Route-Optimizer-Api
```

### 2. Python Environment
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 3. Dependencies
```bash
pip install -r requirements.txt
```

### 4. Environment Configuration
```bash
cp .env.example .env
# .env dosyasını düzenleyerek API key'leri girin
```

### 5. API Keys Gerekli

- **Google API Key**: Directions, Distance Matrix, Places
- **OCM API Key**: Open Charge Map şarj istasyonları
- **OpenWeatherMap API Key**: Hava durumu verileri

## 📡 API Endpoints

### Main Endpoint
```http
POST /optimize_route
Content-Type: application/json

{
  "start_location": "Istanbul, Turkey",
  "end_location": "Ankara, Turkey",
  "vehicle_model_id": "mg4_51kwh",
  "initial_soc_percent": 85,
  "extra_load_kg": 150
}
```

### Health Check
```http
GET /health
```

### Development Endpoints
```http
GET /test          # Test bilgileri
GET /debug         # Debug modu durumu
GET /validate/{vehicle_id}  # Araç validasyonu
```

### API Documentation
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## 🚗 Desteklenen Araçlar

| Vehicle ID | Model | Battery | Consumption | Connector |
|------------|-------|---------|-------------|-----------|
| `mg4_51kwh` | MG4 Electric 51 kWh | 50.8 kWh | 159 Wh/km | CCS |
| `tesla_model_3_long_range` | Tesla Model 3 LR | 75.0 kWh | 145 Wh/km | CCS |
| `opel_frontera_44` | Opel Frontera 44 kWh | 43.8 kWh | 183 Wh/km | CCS |

## 📝 Request/Response Örnek

### Request
```json
{
  "start_location": "Istanbul, Turkey",
  "end_location": "Ankara, Turkey",
  "vehicle_model_id": "mg4_51kwh",
  "initial_soc_percent": 85,
  "extra_load_kg": 150
}
```

### Response
```json
{
  "status": "success",
  "total_distance_km": 452.0,
  "total_duration_minutes": 300.0,
  "total_co2_savings_kg": 35.2,
  "charge_stops": 0,
  "legs": [
    {
      "type": "drive",
      "start_location": "Istanbul, Turkey",
      "end_location": "Ankara, Turkey",
      "distance_km": 452.0,
      "duration_minutes": 300.0,
      "consumption_kwh": 71.9
    }
  ],
  "message": "Rota başarıyla planlandı"
}
```

## 🏃‍♂️ Çalıştırma

### Development
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Production
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## 🔧 Konfigürasyon

### Environment Variables

```bash
# Environment
ENVIRONMENT=development
LOG_LEVEL=INFO

# API Keys
GOOGLE_API_KEY=your_google_api_key
OCM_API_KEY=your_ocm_api_key
OPENWEATHER_API_KEY=your_weather_api_key

# Cache TTL (seconds)
CACHE_TTL_OCM=14400
CACHE_TTL_WEATHER=3600
CACHE_TTL_GOOGLE_PLACES=86400
CACHE_TTL_GOOGLE_DIRECTIONS=3600

# Route Selection
ROUTE_SELECTOR_THRESHOLD_PERCENT=0.10

# CO2 Calculation
CO2_AVG_ICE_CONSUMPTION=7.0
CO2_REGION_MULTIPLIER_TR=1.1

# Data Logging
ENABLE_DATA_LOGGING=true
```

## 📊 Algoritma

### Station Funnel (6 Adım)
1. **Data Collection**: OCM ve Weather API'lerden paralel veri çekimi
2. **Filtering**: Operasyonel ve connector uyumlu istasyonlar
3. **DC/AC Separation**: DC hızlı şarj öncelikli
4. **Distance Filter**: Haversine ile 50km filtreleme
5. **Real-time Check**: Google Distance Matrix ile 15 dakika kontrolü
6. **Scoring**: Ağırlıklı skorlama (deviation + power + rating)

### Consumption Calculation
- Elevation verisi (Google Elevation API)
- Hava durumu etkisi (sıcaklık, rüzgar)
- Araç özellikleri (ağırlık, batarya, HVAC)
- Yük etkisi (extra_load_kg)

## 🧪 Test

```bash
# Test request gönder
curl -X POST "http://localhost:8000/optimize_route" \
  -H "Content-Type: application/json" \
  -d @test_request.json

# Health check
curl "http://localhost:8000/health"
```

## 📁 Proje Yapısı

```
Ev-Route-Optimizer-Api/
├── app/
│   ├── main.py                 # FastAPI uygulaması
│   ├── models.py               # Pydantic modelleri
│   ├── route_planner.py        # Ana rota planlama mantığı
│   ├── route_selector.py       # Google Directions entegrasyonu
│   ├── station_finder.py       # Şarj istasyonu optimizasyonu
│   ├── sustainability_calculator.py # CO2 hesaplamaları
│   ├── services/               # External API servisleri
│   │   ├── google_service.py   # Google Maps API
│   │   ├── ocm_service.py      # Open Charge Map
│   │   └── weather_service.py  # OpenWeatherMap
│   ├── consumption_engine/     # Tüketim hesaplama
│   │   ├── main_calculator.py  # Ana tüketim motoru
│   │   └── vehicle_models.py   # Araç veritabanı
│   └── utils/                  # Yardımcı modüller
│       ├── config_manager.py   # Konfigürasyon yönetimi
│       ├── logger.py           # Structured logging
│       └── data_logger.py      # ML training data
├── notebooks/                  # ML training data
├── tests/                      # Testler
├── .env.example               # Environment şablonu
├── requirements.txt           # Python bağımlılıkları
└── README.md                  # Bu dosya
```

## 🔮 Gelecek Planları

- **V2**: ML-powered route selection
- **V2**: Predictive charging optimization
- **V2**: Real-time traffic integration
- **V3**: Mobile app backend
- **V3**: Fleet management features

## 🤝 Katkı

1. Fork yap
2. Feature branch oluştur (`git checkout -b feature/amazing-feature`)
3. Commit yap (`git commit -m 'Add amazing feature'`)
4. Push yap (`git push origin feature/amazing-feature`)
5. Pull Request aç

## 📄 Lisans

Bu proje MIT lisansı altında dağıtılmaktadır.

## 📞 İletişim

- **Issues**: GitHub Issues
- **Documentation**: `/docs` endpoint
- **API Status**: `/health` endpoint

---

**Version**: v1.3 Enterprise  
**Last Updated**: 2025-11-27  
**Status**: Production Ready 🚀


