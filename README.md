# ⚡ EV Route Optimizer API

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Intelligent Electric Vehicle Route Planning with Real-Time Charging Optimization**

A production-ready FastAPI service that generates optimized routes for electric vehicles, including charging stop planning, real-time consumption estimation, and smart station selection.

![EV Route Optimizer UI](static/preview.png)

---

## 🎯 Key Features

| Feature | Description |
|---------|-------------|
| **🗺️ Multi-Stop Route Planning** | Optimized routes with automatic charging stop placement |
| **⚡ Smart Station Selection** | 6-step funnel algorithm with weighted scoring |
| **📊 Physics-Based Consumption** | Elevation, weather, load, and driving style factors |
| **🌦️ 2-Pass Weather Planning** | ETA-based forecast for accurate consumption |
| **🔄 Crowd-Sourced Reliability** | 3-strike rule for unreliable station blocking |
| **🎨 Modern Cyberpunk UI** | Beautiful dark theme with neon effects |
| **🌱 CO2 Savings Tracking** | Environmental impact vs. ICE vehicles |

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Git**
- API Keys: Google Maps, OpenWeatherMap, Open Charge Map (optional)

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/Ev-Route-Optimizer-Api.git
cd Ev-Route-Optimizer-Api

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Configuration

Create a `.env` file with:

```env
# Required API Keys
GOOGLE_API_KEY=your_google_maps_api_key
OPENWEATHER_API_KEY=your_openweather_api_key
OCM_API_KEY=your_open_charge_map_key  # Optional fallback

# Environment
ENVIRONMENT=development
LOG_LEVEL=INFO
```

### Run

```bash
# Development
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Production
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Access

| URL | Description |
|-----|-------------|
| `http://localhost:8000` | Web UI |
| `http://localhost:8000/docs` | Swagger API Documentation |
| `http://localhost:8000/health` | Health Check |

---

## 📡 API Usage

### Route Optimization

```http
POST /optimize_route
Content-Type: application/json

{
  "start_location": {"lat": 41.0082, "lon": 28.9784},
  "end_location": {"lat": 39.9334, "lon": 32.8597},
  "vehicle_model_id": "tesla_model_3_long_range",
  "current_soc_percent": 85,
  "route_strategy": "optimal"
}
```

### Route Strategies

| Strategy | Description |
|----------|-------------|
| `optimal` | Balance of time and energy efficiency (default) |
| `fastest` | Minimize total travel time (with real-time traffic) |
| `efficient` | Minimize energy consumption |
| `cheapest` | Minimize charging costs (real pricing from 15 Turkish networks) |

### Charging Tariffs

Real pricing data from `data/charging_tariffs.json`:

| Network | DC Price (TL/kWh) |
|---------|-------------------|
| ZES | 9.99 - 12.99 |
| Aksa Şarj | 9.99 - 10.99 |
| Trugo (TOGG) | 10.60 - 11.82 |
| Eşarj | 12.90 - 13.70 |
| Tesla | 12.50 |
| Unknown | 11.00 (default) |

### Station Feedback

```http
POST /station_feedback
Content-Type: application/json

{
  "station_id": "ChIJ...",
  "user_id": "user_123",
  "reason": "out_of_service"
}
```

---

## 🗂️ Dataset

### Vehicle Catalog

- **Source**: [KilowattApp/open-ev-data](https://github.com/KilowattApp/open-ev-data) (MIT License)
- **Vehicles**: 1,321 models with full specifications
- **Charge Curves**: 876 real + estimated curves for accurate charging time calculation

### Data Files

```
data/
├── processed/
│   ├── vehicles_master.json    # Normalized vehicle specs
│   └── charge_curves.json      # SOC → Power curves
└── raw/                        # Original data (gitignored)
```

### Update Dataset

```bash
python scripts/download_open_ev_data.py
python scripts/convert_to_master.py
```

---

## 🏗️ Architecture

```
app/
├── main.py                    # FastAPI application & endpoints
├── route_planner.py           # Main orchestration (13-step flow)
├── station_finder.py          # Station search & scoring
├── soc_simulator.py           # SOC simulation & hotspot detection
├── services/
│   ├── google_service.py      # Google Maps API integration
│   ├── weather_service.py     # OpenWeatherMap integration
│   ├── feedback_service.py    # 3-strike reliability system
│   └── station_logic/         # Extracted filter & scorer
├── consumption_engine/
│   ├── main_calculator.py     # Physics-based consumption
│   └── charging_model.py      # Charge time calculation
├── infrastructure/
│   └── vehicle_catalog/       # Vehicle data access layer
└── constants.py               # Centralized constants (DRY)
```

### Key Algorithms

1. **Station Funnel (6 Steps)**
   - Data collection from Google Places + OCM
   - Operational & connector filtering
   - DC/AC separation with DC priority
   - Distance filtering (Haversine)
   - Real-time deviation check (Distance Matrix)
   - Weighted scoring (deviation, power, rating, amenities)

2. **Consumption Engine**
   - Base consumption from vehicle specs
   - Elevation impact (regenerative braking)
   - Weather factors (temperature, wind, rain)
   - Load impact (passengers, cargo)
   - HVAC consumption estimation

3. **3-Strike Feedback System**
   - Users can report problematic stations
   - 3 unique reports = 48-hour temporary block
   - Automatic unblock after expiry
   - Block extension on new reports

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test category
pytest tests/test_core_logic.py::TestFeedbackManager -v

# Test coverage
pytest tests/ --cov=app --cov-report=html
```

### Test Categories

- **FeedbackManager**: 3-strike rule, spam protection, block expiry
- **SOC Parameters**: Dynamic tolerance, distance-based thresholds
- **StationScorer**: Weighted rating, amenity bonuses
- **StationFilter**: Connector compatibility, power filtering

---

## 🔮 Roadmap

### Completed ✅
- [x] Multi-source station search (Google + OCM)
- [x] 2-pass weather-aware consumption
- [x] Crowd-sourced station reliability (3-strike)
- [x] Physics-based consumption engine
- [x] Real-time traffic integration
- [x] Modern cyberpunk UI

### Planned 📋
- [ ] Machine learning route prediction
- [ ] Real-time pricing integration
- [ ] Mobile app (React Native)
- [ ] Fleet management dashboard
- [ ] Multi-language support
- [ ] Offline route caching

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

### Third-Party Attributions

- **Vehicle Data**: [KilowattApp/open-ev-data](https://github.com/KilowattApp/open-ev-data) (MIT)
- **Icons**: [Lucide Icons](https://lucide.dev) (ISC)
- **Fonts**: [Inter](https://fonts.google.com/specimen/Inter) (OFL)

---

## 📞 Support

- **Documentation**: `/docs` endpoint (Swagger UI)
- **Health Check**: `/health` endpoint
- **Issues**: [GitHub Issues](https://github.com/yourusername/Ev-Route-Optimizer-Api/issues)

---

**Version**: 3.0  
**Last Updated**: December 2024  
**Status**: Production Ready 🚀
