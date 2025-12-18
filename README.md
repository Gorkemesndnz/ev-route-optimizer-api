# EV Route Optimizer API v1.3

Akıllı Elektrikli Araç Rota Optimizasyonu Servisi

Bu proje, elektrikli araçlar için **rota + şarj planı + tüketim tahmini** üreten bir FastAPI servisidir.

Temel hedef:

- **En gerçekçi tüketim hesabı** (eğim + hava durumu + yük) ile SOC simülasyonu
- **Şarj duraklarını akıllı seçip** (sapma, güç, rating/yorum güveni ve tesis olanakları) kullanıcıya uygulanabilir bir plan vermek
- **Varış zamanı (ETA)** faktörünü hem hava durumu hem de durak bağlamında kullanarak daha doğru sonuç üretmek

Servis;

- `POST /optimize_route` ile rota, bacaklar (drive/charge), tüketim ve hedef SOC değerlerini döner
- `GET /` altında basit bir **Web UI** sunar (rota sonucu + durak detayları)

Son sürümlerde eklenen en önemli iyileştirmeler:

- **2-Pass planlama**: Durak seçimi ile tüketim arasındaki döngüyü kırmak için önce kaba plan, sonra durak hava durumu ile refine tüketim
- **ETA bazlı forecast weather**: Varış noktası ve şarj durakları için “şu an” yerine **varış anına en yakın forecast**
- **Weighted rating + amenities skoru (V2.8)**: Az yorumlu rating’leri yumuşatan puanlama + WC/Yemek/Market/Otopark/Açık bonusları
- **UI iyileştirmeleri**: İstasyon kaynağı (Google/OCM), rating/yorum sayısı, vicinity ve amenities etiketleri

## 📌 Durum / Versiyon

- **API**: FastAPI
- **Web UI**: `GET /` (static UI)
- **Son büyük güncelleme**: **V2.8**

V2.8 ile:

- **2-pass weather refinement** (Pass 2 her zaman çalışır)
- **ETA bazlı forecast** (varış + şarj durakları)
- **İstasyon seçimi için weighted rating + amenities skoru**
- **UI’da istasyon rating/yorum sayısı, kaynak (Google/OCM), vicinity ve amenities gösterimi**

## ✨ Özellikler

- **🗺️ Multi-Stop Route Optimization** - Birden fazla durak destekli rota planlaması
- **⚡ Smart Charging Planning** - OCM ve Google Maps entegrasyonlu şarj istasyonu bulma
- **📊 Real-time Consumption** - Elevation, hava durumu ve araç özelliklerine göre tüketim hesabı
- **🌦️ 2-Pass Weather Consumption** - Şarj duraklarındaki hava durumu ile tüketimi refine eden 2-pass planlama
- **🕒 ETA-based Forecast Weather** - Varış ve şarj durakları için varış zamanına göre forecast seçimi
- **🌱 CO2 Savings** - Benzinli araçlara karşı çevresel tasarruf analizi
- **🔍 Station Funnel Algorithm** - 6 adımlı istasyon seçim ve skorlama algoritması
- **⭐ Weighted Rating + Amenities Scoring (V2.8)** - Az yorumlu rating’leri yumuşatan puanlama + WC/Yemek/Market/Otopark/Açık bonusları
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

## ✅ Hızlı Başlangıç (Windows PowerShell)

### 0) Gereksinimler

- **Git**
- **Python 3.10+** (3.8+ çalışır ama önerilen 3.10+)

### 1) Repo indir

```powershell
git clone <repository-url>
cd C:\Ev-Route-Optimizer-Api
```

Not: Eğer repoyu farklı bir klasöre clone ettiysen `cd` satırını kendi path’ine göre güncelle:

```powershell
cd <clone_ettigin_klasor>\Ev-Route-Optimizer-Api
```

### 2) Virtualenv oluştur ve aktive et

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Eğer PowerShell `Activate.ps1` engellenirse (ExecutionPolicy):

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\.venv\Scripts\Activate.ps1
```

### 2b) Alternatif: Windows CMD ile activate

```bat
python -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
```

Repo klasörüne girmek için (CMD):

```bat
cd C:\Ev-Route-Optimizer-Api
```

### 3) Bağımlılıkları kur

```powershell
pip install -r requirements.txt
```

Windows CMD:

```bat
pip install -r requirements.txt
```

### 4) .env oluştur

```powershell
Copy-Item .env.example .env
notepad .env
```

Windows CMD:

```bat
copy .env.example .env
notepad .env
```

### 5) API Key’leri gir

- **Google API Key**: Directions + Distance Matrix + Places
- **OpenWeatherMap API Key**: Current + Forecast
- **OCM API Key**: Opsiyonel (Google Places boş dönerse fallback)

`.env` örneği:

```bash
GOOGLE_API_KEY=your_google_api_key
OPENWEATHER_API_KEY=your_openweather_api_key
OCM_API_KEY=your_ocm_api_key
ENVIRONMENT=development
LOG_LEVEL=INFO
```

### 6) Çalıştır

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows CMD:

```bat
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 7) Aç

- **Web UI**: `http://127.0.0.1:8000/`
- **Swagger**: `http://127.0.0.1:8000/docs`
- **Health**: `http://127.0.0.1:8000/health`

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
  "start_location": {"lat": 41.0082, "lon": 28.9784},
  "end_location": {"lat": 39.9334, "lon": 32.8597},
  "vehicle_model_id": "mg4_51kwh",
  "current_soc_percent": 85,
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
  "start_location": {"lat": 41.0082, "lon": 28.9784},
  "end_location": {"lat": 39.9334, "lon": 32.8597},
  "vehicle_model_id": "mg4_51kwh",
  "current_soc_percent": 85,
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
  "start_weather": {"temp_c": 6.1, "condition": "cloudy", "wind_speed_mps": 2.3},
  "end_weather": {"temp_c": 3.0, "condition": "cloudy", "wind_speed_mps": 1.2},
  "legs": [
    {
      "type": "drive",
      "distance_km": 452.0,
      "duration_minutes": 300.0,
      "consumption_kwh": 71.9
    }
  ],
  "message": "Rota başarıyla planlandı"
}
```

## 🌦️ Hava Durumu Mantığı (V2.7+)

- **Başlangıç**: `current weather`
- **Varış**: `forecast weather` (ETA bazlı)
- **Şarj istasyonları**: `forecast weather` (ETA bazlı) → `ChargeLeg.weather_context`

### 2-Pass Planning

- **Pass 1**: Start (current) + End (forecast) ile ortalama weather → ilk tüketim ve durak adayları
- **Pass 2**: Durak lokasyonlarının hava durumu ile tüketimi yeniden hesaplar

Not: **Pass 2 için 2°C eşiği kaldırıldı** → refined weather varsa Pass 2 her zaman çalışır.

## ⭐ İstasyon Seçimi (Google-first) + Weighted Rating + Amenities (V2.8)

### Veri Kaynakları

- **Google Places (öncelikli)**
- **Open Charge Map (fallback)**: Google boş dönerse devreye girer

### Weighted Rating

Az yorumlu rating’lerin güveni düşük olduğundan, rating bir prior ile karıştırılır:

```
weighted = confidence * rating + (1 - confidence) * prior
confidence = min(1, user_ratings_total / 50)
prior = 3.5
```

### Amenities Skoru

WC / yemek / market / otopark / açık olma bilgileri istasyon skoruna eklenir.

### Skor Ağırlıkları (özet)

- **Sapma (deviation)**: 0.40
- **Güç (power)**: 0.25
- **Rating (weighted)**: 0.15
- **Amenities**: 0.20

## 🧪 Test (PowerShell)

### Health check

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get
```

### Route request örneği

```powershell
$body = @{
  start_location = @{ lat = 41.0082; lon = 28.9784 }
  end_location   = @{ lat = 39.9334; lon = 32.8597 }
  vehicle_model_id = "tesla_model_3_long_range"
  current_soc_percent = 80
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8000/optimize_route" -Method Post -ContentType "application/json" -Body $body
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
6. **Scoring**: Ağırlıklı skorlama (deviation + power + weighted rating + amenities)

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

**Version**: v2.8  
**Last Updated**: 2025-12-18  
**Status**: Production Ready 🚀


