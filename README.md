# ⚡ EV Route Optimizer API

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Yapay Zeka Destekli, Gerçek Zamanlı ve Fizik Tabanlı Elektrikli Araç (EV) Rota Planlama Motoru**

EV Route Optimizer API, elektrikli araç sahipleri için uçtan uca, menzil kaygısını (range anxiety) ortadan kaldıran production-ready bir FastAPI servisidir. Araç modeli, anlık hava durumu, rakım (elevation), yolcu/yük durumu ve canlı trafik gibi verileri kullanarak en optimum şarj noktalarını ve sürüş rotasını hesaplar. 

Üstelik V3.0 sürümü ile birlikte, topluluk odaklı istasyon geri bildirim sistemi (3-Strike) ve dinamik şarj fiyatlandırması (15+ Türkiye Şarj Ağı) özelliklerine kavuşmuştur.

---

## 📑 İçindekiler
1. [V3.0 ile Gelen Yenilikler](#v30-ile-gelen-yenilikler)
2. [Sistem Gereksinimleri](#sistem-gereksinimleri)
3. [Kurulum Adımları (Windows / Linux / macOS)](#kurulum-adımları)
4. [Nasıl Çalıştırılır?](#nasıl-çalıştırılır)
5. [Güvenlik Önlemleri ve Limitler](#güvenlik-önlemleri-ve-limitler)
6. [Dosya Yapısı ve Dosyaların Amacı](#dosya-yapısı-ve-dosyaların-amacı)
7. [Araç Veriseti ve Kaynağı](#araç-veriseti-ve-kaynağı)
8. [Mimari ve Hesaplama Katmanları (Nasıl Çalışır?)](#mimari-ve-hesaplama-katmanları-nasıl-çalışır)
9. [API Kullanımı ve Endpointler](#api-kullanımı-ve-endpointler)
10. [Testler](#testler)

---

## 🚀 V3.0 ile Gelen Yenilikler
* **Crowd-Sourced İstasyon Güvenilirlik Sistemi:** 3-strike kuralı ile hatalı/çalışmayan istasyonlar algılanır ve 48 saatliğine algoritmadan dışlanır. Spam koruması ve süre uzatma mevcuttur.
* **Fiyatlandırma Servisi (PricingService):** ZES, Eşarj, Trugo, Tesla, Shell Recharge başta olmak üzere 15 farklı şarj operatörünün güncel `data/charging_tariffs.json` üzerinden fiyat hesaplaması yapılır. `cheapest` stratejisi şarj maliyetini en aza indirir.
* **Dinamik SOC Toleransı:** Hedef SOC'ye kısa/uzun mesafe bazlı %8-15 tolerans aralıklarıyla esnek yaklaşılır. Gereksiz kısa süreli şarjları önler.
* **Modern Cyberpunk UI:** Backend'e bağlı çalışan `static/` içindeki modern Glassmorphism, neon glow ve scanline tasarımları barındıran dashboard ekranı.
* **2-Pass Weather Refinement:** Varış ve şarj noktalarının ETA (Tahmini Varış Süresi) hesaplanarak forecast (gelecek hava tahmini) datası alınır; tüketim çift tur (2-pass) kontrolle en yüksek doğruluğa ulaşır.
* **Tesis (Amenities) Bazlı İstasyon Puanlaması:** Şarj altyapısı kadar etrafındaki market, tuvalet, otopark, yemek gibi olanaklar da tespit edilip istasyonlara bonus skor kazandırır.

---

## 💻 Sistem Gereksinimleri

Projeyi kendi ortamınızda çalıştırmak için aşağıdaki gereksinimlere sahip olmanız gerekir:

- **OS:** Windows 10/11, macOS, veya Linux (Ubuntu / Debian tabanlı).
- **Python:** `3.10` veya üzeri.
- **Git** (Depoyu klonlamak için).
- **API Anahtarları (Environment Variables):**
  - **Google Maps API Key:** (Directions, Elevation, Distance Matrix, Places, Geocoding)
  - **OpenWeatherMap API Key:** (Anlık hava ve Forecast)
  - *(Opsiyonel)* **Open Charge Map API Key:** OCM verisi limitlerine takılmamak için kullanılabilir.

---

## 🛠️ Kurulum Adımları

Proje bağımlılıklarını izole etmek için **Sanal Ortam (Virtual Environment)** kullanılması zorunludur.

### Windows (PowerShell)
```powershell
# 1. Depoyu klonlayın
git clone https://github.com/yourusername/Ev-Route-Optimizer-Api.git
cd Ev-Route-Optimizer-Api

# 2. Sanal ortamı oluşturun ve aktif edin
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3. Pip'i güncelleyin ve bağımlılıkları yükleyin
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Çevresel değişken (Environment Variables) dosyasını oluşturun
Copy-Item .env.example .env
# .env dosyasını bir metin düzenleyiciyle açarak API anahtarlarınızı girin.
```

### Linux / macOS (Bash)
```bash
# 1. Depoyu klonlayın
git clone https://github.com/yourusername/Ev-Route-Optimizer-Api.git
cd Ev-Route-Optimizer-Api

# 2. Sanal ortamı oluşturun ve aktif edin
python3 -m venv venv
source venv/bin/activate

# 3. Pip'i güncelleyin ve bağımlılıkları yükleyin
python -m pip install --upgrade pip
pip install -r requirements.txt

# 4. Çevresel değişken (Environment Variables) dosyasını oluşturun
cp .env.example .env
# nano .env diyerek API anahtarlarınızı girin.
```

> **Önemli Not:** API anahtarlarınızı asla `.env` hariç başka bir konumda (özellikle repoya gönderilecek dosyalarda) hardcoded tutmayın.

---

## ▶️ Nasıl Çalıştırılır?

Backend'i uvicorn ile asenkron olarak ayağa kaldırmak için sanal ortam (venv) aktifken aşağıdaki komutu çalıştırın:

**Geliştirici Modu (Hot-Reload Açık):**
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**Prodüksiyon Modu (Güçlü Performans, Logları Minimize Eder):**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Sunucu başladıktan sonra aşağıdaki adreslerden erişim sağlayabilirsiniz:
- **Frontend (Web UI):** [http://localhost:8000](http://localhost:8000)
- **API Dokümantasyonu (Swagger UI):** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check:** [http://localhost:8000/health](http://localhost:8000/health)

---

## 🛡️ Güvenlik Önlemleri ve Limitler

Projeyi dışa açıyorsanız veya public kullanacaksanız bazı dahili ve yapılandırma güvenlik önlemleri alınmıştır:
- **Environment Isolation:** Tüm hassas bilgiler (API Anahtarları) `.env` üzerinden yüklenmektedir. `python-dotenv` ile güvenli şekilde isolate edilir.
- **XSS ve Injection Koruması:** FastAPI'nin sunduğu Pydantic modelleri tüm gelen verileri katı tipler (strict typing) üzerinden filter eder. İstenmeyen bir veri modeli geldiğinde `422 Unprocessable Entity` döner, internal hataları açığa çıkarmaz.
- **Feedback Spam Koruması:** `FeedbackManager` içerisinde aynı IP/Kullanıcının 24 saat içerisinde aynı istasyon için mükerrer rapor göndermesi engellenmiştir.
- **Dış API Güvenilirliği (Rate Limiting/Fallback):** `services/` altındaki entegrasyonlarda (Google, Weather, OCM) belirli TTL süreli `lru_cache` kullanılarak gereksiz API call atışları ve DoS riskleri minimize edilmiştir.

---

## 📂 Dosya Yapısı ve Dosyaların Amacı

Mevcut proje genişletilebilir (scalable) modüler bir yapıya sahiptir.

```text
Ev-Route-Optimizer-Api/
├── app/                           # Backend Uygulama Ana Klasörü
│   ├── main.py                    # FastAPI giriş noktası. Endpointlerin tanımlandığı ana controller.
│   ├── models.py                  # Pydantic veri modelleri (GeoPoint, RouteRequest, StationInfo vd.). Veri sözleşmeleri.
│   ├── constants.py               # Merkezi sabitler (Güç eşikleri, toleranslar, SOC ayarları). DRY Prensibi.
│   ├── route_planner.py           # Orkestratör: API ve algoritmaları entegre edip ana sonucu (`plan_route`) üretir.
│   ├── route_selector.py          # Google Directions listesinden 'Verimli' veya 'Popüler' rotayı batarya kapasitesine göre seçer.
│   ├── route_segmenter.py         # Poligon verisini (Polyline) decode eder ve küçük segmentlere böler.
│   ├── soc_simulator.py           # Batarya simülasyonunu yapar (SOCSimulator) ve durakları planlar (ChargePlanOptimizer).
│   ├── charging_model.py          # Araç SOC'sine ve hava durumuna göre şarj hızı/süresini tespit eden matematiksel model.
│   ├── station_finder.py          # Hotspot noktaları yakınındaki istasyonları arayan modül (OCM + Google filter ile).
│   ├── services/                  # Dış Web Servisleri (Adaptörler)
│   │   ├── google_service.py      # Google Maps, GeoCode, Elevation, Distance Matrix çağrıları.
│   │   ├── ocm_service.py         # Open Charge Map üzerinden canlı şarj noktası verisi çeker.
│   │   ├── weather_service.py     # OpenWeatherMap üzerinden hava durumu/forecast çeker.
│   │   ├── feedback_service.py    # 3-strike istasyon bildirimi ve geçici engelleme altyapısı.
│   │   └── station_logic/         # İstasyon filtreleme (filter.py) ve skorlama (scorer.py) algoritmaları.
│   └── consumption_engine/        # Ev Tüketim Motoru (Çekirdek Algoritmalar)
│       ├── main_calculator.py     # Yük, rüzgar, ısı, rakım değişkenlerini birleştirerek segment kWh tüketimini çıkartır.
│       ├── vehicle_models.py      # Araç veritabanı. `VEHICLE_DB` ve taşıt parametreleri.
│       └── v1_rule_based/         # Fizik denklemleri ve kural tabanlı yansımalar (Elevation, Weather, Load).
├── data/                          # Lokâl Veri Kaynakları
│   └── charging_tariffs.json      # 15+ firmanın şarj fiyatları.
├── static/                        # Frontend Dosyaları (Vanilla JS + Tailwind + CSS)
│   ├── index.html                 # UI Gösterimi.
│   ├── css/, js/                  # JS bileşenleri (geocoding, ui, map, app)
├── tests/                         # Pytest birim/entegrasyon testleri (50+ Tane)
├── README.md                      # Bu dosya!
├── .env.example                   # .env şablon dosyası.
└── requirements.txt               # Bağımlılık paketleri.
```

---

## 🚗 Araç Veriseti ve Kaynağı

Proje içerisindeki elektrikli araç modelleri ve bu araçların spesifik şarj eğrileri rastgele değil, gerçek veriler üzerine inşa edilmiştir.

### 1. Veriseti Kaynağı
Araç veri tabanımız **[KilowattApp/open-ev-data](https://github.com/KilowattApp/open-ev-data)** adlı açık kaynaklı (MIT Lisanslı) dataset projesinden beslenmektedir. Bu repodan düzenli aralıklarla araç katalogları ve batarya teknik verileri (charge curves, batarya tipleri) senkronize edilmektedir.

### 2. Veri İçeriği
 Sistem içerisinde **1,300'den fazla** güncel EV (Elektrikli Araç) modeli ve **800'ün üzerinde** eşsiz şarj eğrisi grafiği bulunur:
- **Batarya Kapasitesi (kWh):** Hesaplamaların omurgasını oluşturan maksimum pil kapasitesi.
- **Base Consumption (Wh/km):** Araçların test döngülerinden çıkmış, rüzgarsız ve düz yoldaki ideal fabrika tüketim verisi.
- **Ağırlık (Curb Weight):** Fizik motorunda (yukarı doğru yokuş tırmanırken yapılan iş `mgh`) arabanın kendi ağırlığını temsil eder.
- **Maks. DC Hızı ve Soket Tipi:** Aracın en fazla ne kadar hızlı şarj olabileceği ve soketinin (CCS2, Type 2, CHAdeMO) istasyon filtrelerindeki uyumluluğu.

### 3. Nasıl Güncellenir?
Eğer ileride piyasaya çıkacak yeni araç modellerini sisteme entegre etmek isterseniz, proje kök dizininde yer alan `scripts` klasöründeki yardımcı betikleri çalıştırarak güncel veritabanını master dosyamıza indirebilirsiniz:
```bash
# Repo'dan en güncel ham (raw) verileri çeker
python scripts/download_open_ev_data.py

# Verileri kendi sistemimizin okuyabileceği formata dönüştürür
python scripts/convert_to_master.py
```
*(Yukarıdaki betikler `data/processed/` klasörü altındaki `vehicles_master.json` ve `charge_curves.json` dosyalarını otomatik olarak güncelleyecektir.)*

---

## 🧠 Mimari ve Hesaplama Katmanları (Nasıl Çalışır?)

Algoritma tamamen şeffaf, fizik gerçeklerine dayalı ardışık bir boru hattından (pipeline) geçer:

1. **Orijin-Destinasyon Geocoding:** Kullanıcının yazdığı text'ler (`static/js/geocoding.js` aracılığıyla) backend'e, oradan Google API ile enlem/boylam koordinatlarına dönüşür.
2. **Route Selector (Rota Alternatifleri Seçimi):**
   * Google Maps API'sinden olası yollar (Otoyol, dağ yolu vs) çekilir.
   * `route_selector.py` pil kapasitesine bakarak "Trafiksiz popüler yol" mu yoksa "Mesafesi çok daha kısa verimli yol" mu sorusuna dinamik enerji tasarrufu/eşik analizine bakarak karar verir.
3. **Route Segmenter (Segmentasyon):** `route_segmenter.py` ile bu yol, Google'dan dönen zikzaklı Polyline kırılarak ortalama 10 km'lik minik analiz parçacıklarına bölinür. Rakım (elevation) verileri bu segmentlere paylaştırılır.
4. **ETA & 2-Pass Weather (Hava Durumu Öngörüsü):** Start noktasında "Şu anki hava" (Current), varış ve tahmini mola noktalarındaki tahmini sürelere göre (Forecast) hava durumu `weather_service.py` ile alınır.
5. **Consumption Engine (Fizik Tüketim Motoru):** 
   - Araç spesifikasyonları baz alınarak her segmentin sürtünmesi (rüzgar açısı, hızı), yerçekimi (yokuş çıkarken kayıp, inerken regen-kazanım), harici yük (yolcu + valiz) tüketimi `main_calculator.py` ile birleştirilir.
6. **SOC Simulator & ChargePlanOptimizer:**  `soc_simulator.py` sanal bir araç sürüyormuş gibi davranıp "% SOC düştü, pil bitiyor, buralarda şarj lazım" diyerek haritaya **Hotspot (Isı noktaları)** atar.  
   - 65% ile 95% aralığındaki SOC doldurma ihtimalleri tek tek matematiksel skorla çarpıştırılır (**Heuristic Reward Minimization**). En az kaybedilen zaman ve ideal durak aralığına sahip şarj senaryosu seçilir.
7. **Station Finder (İstasyon Puanlama):** Bulunan optimal durak çevresindeki istasyonlar filtrelenir (bozuk olanlar çöpe atılır, aracın soketine uymayanlar elenir); **güç, restoran varlığı, rota sapma süresi, fiyat** değerlendirilerek en optimal istasyon "kesin şarj durağı" ilan edilir.

### İleri Fizik Motoru Özellikleri

Menzil simülasyonunun sapmasını minimize etmek için araç özellikleri dışındaki doğa koşulları ve risk faktörleri aşağıdaki yöntemlerle analiz edilir:

#### 1. Safe Harbor (Güvenli Liman / Ölü Rota Koruması)
Varış noktanızın etrafında (örneğin doğada bir kamp alanı) hiç şarj istasyonu olmayabilir. Böyle bir durumda araç, pilini tamamen tüketip orada mahsur kalmamalıdır.
* Algoritma, hedefe (varışa) ulaşmadan önce etraftaki en son şarj istasyonlarını tarar. 
* Arka planda **Ghost Leg (Ölü Rota)** adlı sanal bir dönüş rotası hesaplatır. Yani, "Hedefe vardıktan sonra en yakın istasyona geri dönmek için ne kadar enerji yakılır?" 
* Buna göre **"Varış SOC'si" (Hedefte kalması gereken min şarj)** dinamik olarak belirlenir. Kamp alanına minimum %5 ile varmak yerine, %18 ile varmaya zorlar ki aracınız dönüş için "Safe Harbor" (kurtarıcı) istasyona gidebilsin. 

#### 2. Dinamik Rakım (Elevation) Hesaplaması
Rota düz bir vektör olarak çizilmez. Google Elevation verileriyle yol boyunca kazanılan (yokuş yukarı) veya kaybedilen (yokuş aşağı) rakım metrekare/metrekare analiz edilir:
* Yokuş tırmanırken yerçekimi aracın kütlesine (ve içindeki ektra yolcu ağırlığına) direneceği için tüketim artar. Formül: `mgh`.
* Yokuş inerken ise aracın EV dinamosu **Rejeneratif Fren (Regen)** yaparak enerjiyi depolar. Tüketim eksi (-) değere düşerek batarya yüzdesini artırabilir.

#### 3. Çift Turlu ve Canlı Hava Durumu Analizi (2-Pass Weather)
Sıcaklık ve rüzgar bataryayı dramatik ölçüde etkiler.
* Algoritma rüzgar hızını ve rüzgarın araca vuruş açısını (headwind, tailwind, crosswind) dikkate alarak sürtünme faktörü (drag factor) uygular. 
* Hava sıcaklığının düşüklüğü pil hücrelerinin kimyasal direncini artıracağından menzil kaybedilir (örn. <-10 derecede yüksek penaltı kesilir).
* Uzun süreli yollarda anlık (current) hava durumuna bakmak yanıltıcıdır. Rota süresi 5 saat ise, hedefe varıldığındaki saat öngörülür (ETA) ve hedefin **Forecast (Tahmini)** hava verisine göre enerji tüketimi ikinci bir turla (2-pass) yeniden ölçeklendirilir.

---

## 📡 API Kullanımı ve Endpointler

Tüm endpoint'ler Swagger UI (`/docs`) üzerinden dinamik test edilebilir.

### 1. Rota Optimizasyon Endpoint'i
`POST /optimize_route`

```json
{
  "start_location": {"lat": 41.0082, "lon": 28.9784},
  "end_location": {"lat": 39.9334, "lon": 32.8597},
  "vehicle_model_id": "tesla_model_3_long_range",
  "current_soc_percent": 85,
  "route_strategy": "optimal",
  "passenger_count": 2,
  "child_count": 0,
  "extra_load_kg": 50,
  "preferences": {
      "max_detour_km": 10,
      "preferred_plug_types": ["CCS2"],
      "amenities_required": ["toilet", "food"]
  }
}
```

### 2. İstasyon Bildirim Endpoint'i (Geri Bildirim)
`POST /station_feedback`

```json
{
  "station_id": "OCM_12345",
  "user_id": "hashed_user_id_99",
  "reason": "out_of_service"
}
```

---

## 🧪 Testler

Sistem içerisinde Logic, Algoritma ve Bağımlılık testlerini içeren kapsamlı `pytest` suitleri bulunur.

```bash
# Tüm testleri çalıştırmak için
pytest tests/ -v

# Sadece spesifik bir modülü test etmek için
pytest tests/test_core_logic.py::TestFeedbackManager -v

# Kod kapsama durumunu HTML rapor ile incelemek için (coverage paketi gerektirir)
pytest tests/ --cov=app --cov-report=html
```

Test Dosyalarının Odakları:
- `TestFeedbackManager`: 3-strike mekanizması kontrolü, süre dolumu, spam analizi.
- `TestSOCLogic`: Dinamik batarya senaryoları.
- `TestStationScorer`: İstasyon ağırlıkları, Amenity puanlamalarının doğruluğu.

---

## 🤝 Katkıda Bulunma ve Lisans

Bu proje **MIT Lisansı** ile lisanslanmıştır. Araç verileri Open-EV-Data'dan alınmakta, ikon ve fontlar (Lucide / Inter) kendi serbest açık kaynak lisanslarıyla kullanılmaktadır. Pull Request (PR) yollarken `flake8` standartlarına ve varolan test suitinden sorunsuz geçmesine (`pytest` -> 0 errors) özen gösterin.
