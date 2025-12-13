# EV Route Optimizer – Mimari ve Hesaplama Özeti

## 1. Genel Akış: main → route_planner → alt modüller

- **`app/main.py`**
  - FastAPI uygulaması.
  - Ana endpoint: `POST /optimize_route` → `plan_route` fonksiyonunu çağırıyor (`app.route_planner`).
  - Girdi: `RouteRequest` (başlangıç/bitiş, araç modeli, başlangıç SOC vs.)
  - Çıktı: `MultiStopRouteResponse` (toplam mesafe, süre, tüketim, bacaklar, şarj durakları).

- **Temel veri modelleri (`app/models.py`)**
  - **`GeoPoint`**: lat/lon + opsiyonel adres.
  - **`DriveLeg`**: bir sürüş bacağı (mesafe, süre, hız, elevation, hava durumu, tüketim, SOC).
  - **`ChargeLeg`**: bir şarj seansı (istasyon, harcanan süre, eklenen enerji, maliyet, hava konteksi).
  - **Şarj/istasyon tarafı**: `StationInfo`, `ConnectorInfo`, `ChargerType`, `PlugType`.
  - **Hava durumu**: `WeatherInfo`, `WeatherCondition`.
  - **Cevap modelleri**: `RouteResponse`, `MultiStopRouteResponse`.

Bu modeller, tüm servislerin ve hesaplama katmanlarının konuştuğu ortak şema.

---

## 2. Consumption Engine (Enerji Tüketim Motoru)

### 2.1. Vehicle Models (`app/consumption_engine/vehicle_models.py`)

- **`VehicleModel` dataclass**
  - `curb_weight_kg`
  - `battery_capacity_kwh`
  - `base_consumption_wh_km` (nominal tüketim)
  - `connector_type`, `avg_dc_charge_rate_kw`, `avg_ac_charge_rate_kw`
  - `auxiliary_power_kw` (HVAC/elektronik).

- **`VEHICLE_DB`**
  - Örnek kayıtlar:
    - `mg4_51kwh`
    - `tesla_model_3_long_range`
    - `opel_frontera_44`
- **`get_vehicle_model(model_id)`**
  - Route planner, SOC simulator ve station finder aynı veri tabanını kullanıyor.

### 2.2. Rule-Based Katmanlar

#### Yük Katmanı (`v1_rule_based/load_layer.py`)

- **`LoadEffectCalculator.calculate_mass_factor(...)`**
  - Ek kütle → tüketim çarpanı.
  - Toplam ekstra kütle:
    - Yetişkin: `passenger_count * 75 kg`
    - Çocuk: `child_count * 30 kg`
    - Ek yük: `extra_load_kg`
  - `mass_ratio = total_extra_mass / base_vehicle_weight_kg`
  - `mass_factor = 1 + mass_ratio * MASS_COEFFICIENT` (`MASS_COEFFICIENT = 0.15`)
  - Minimum 1.0’a clamp.

> Teknik olarak: lineer bir **oran tabanlı katsayı**; telemetriye uygun kalibrasyon.

#### Elevation Katmanı (`v1_rule_based/elevation_layer.py`)

- **Fizik temelli hesaplama:**
  - Potansiyel enerji:
    - `E_uphill = m * g * Δh_gain`
    - `E_downhill = m * g * Δh_loss * regen_efficiency`
    - `net_kwh = (E_uphill - E_downhill) / 3_600_000`
- **`calculate_elevation_energy(mass_kg, gain_m, loss_m)`**:
  - Yukarıdaki formül, kWh cinsinden net enerji farkını döner (pozitif = ekstra tüketim, negatif = regen kazanımı).
- **`elevation_factor(...)`**
  - Segment bazında:
    - `base_segment_kwh = base_consumption_per_km * segment_distance_km`
    - `factor = (base_segment_kwh + elevation_kwh) / safe_base`
  - Clamp: `[0.5, 3.0]` (çok dik iniş/çıkışları sınırlar).

> Burada **fiziksel enerji hesabı** (mgh) + **oran bazlı normalizasyon** kullanılıyor.

#### Weather Katmanı (`v1_rule_based/weather_layer.py`)

- **`temperature_factor(temp_c)`**
  - Parçalı lineer/kademeli kural tabanlı fonksiyon:
    - < -10°C → 1.40
    - -10–0 → 1.25
    - 0–10 → 1.15
    - 10–18 → 1.05
    - 18–28 → 1.00
    - 28–40 → 1.10
    - > 40 → 1.20

- **`wind_factor(wind_speed, vehicle_heading, wind_direction)`**
  - Rüzgar açısı → headwind / crosswind / tailwind ayrımı.
  - Fark açısına göre base:
    - Headwind ≤45° → 1.10
    - Crosswind ≤135° → 1.05
    - Tailwind >135° → 0.95 (biraz indirim)
  - Hıza göre katsayı:
    - 0–3 m/s → 1.00
    - 3–7 → 1.05
    - 7–12 → 1.10
    - >12 → 1.20
  - Tailwind için alt/üst clamp: `0.90–1.00`.
  - Genel clamp: `[0.90, 1.30]`.

- **`precipitation_factor(condition)`**
  - Yağmur → 1.10
  - Kar → 1.25
  - Sis → 1.05
  - Diğer → 1.00

- **`calculate_weather_factor(weather, heading)`**
  - `final = temp_f * wind_f * precip_f`
  - Clamp: `[0.9, 2.5]`.

> Burası tamamen **kural tabanlı/heuristic** ama çarpanlar fiziksel gerçeklikle kalibre.

### 2.3. Main Consumption Calculator (`main_calculator.py`)

Burada büyük bir sınıf var, fakat özünde:

- **`ConsumptionResult`**
  - `base_consumption_kwh`
  - `load_adjusted_kwh`
  - `weather_adjusted_kwh`
  - `elevation_energy_kwh`
  - `aux_consumption_kwh`
  - `regen_recovered_kwh`
  - `total_consumption_kwh`
  - `practical_consumption_kwh` (regen limiti ile clamp)
  - `is_net_charging` (net negatif tüketimse True).

- **`MainCalculator`**
  - Baz tüketimi, yük faktörü, hava faktörü, eğim enerjisi ve HVAC tüketimi tek bir fiziksel çerçevede birleştiriliyor.
  - Kullandığı teknikler:
    - Araç baz tüketimini `Wh/km` → `kWh`’a çevirme.
    - `LoadEffectCalculator`, `WeatherEffectCalculator`, `ElevationEffectCalculator` ile **çarpan + ek enerji** kombinasyonu.
    - Negatif toplam (çok uzun iniş) durumunda regen verimliliğiyle sınırlı **minimum pratik tüketim limiti** (`MIN_PRACTICAL_CONSUMPTION = -10 kWh`).

- **`calculate_route_consumption(...)`**
  - `route_planner` içinde import edilmiş:
    - Segment listesi üzerinde dönerek her birine `calculate_segment_consumption` uygular.
    - Toplam tüketimi, segment başına tüketimleri döner.

> Özet: Consumption engine, **fizik + kural tabanlı faktörler** ile segment bazlı enerji tüketimi üretir; ML-ready bir tasarım (araç, hava, yük parametreleri açık).

---

## 3. Charging Model (`app/charging_model.py`)

Gerçekçi bir şarj eğrisi ve planner penaltı modeli.

- **3 fazlı eğri (CC–CV benzeri):**
  - Faz 1: `0–50% SOC` → peak güce yakın, hızlı artış.
  - Faz 2: `50–80%` → güç lineer azalıyor.
  - Faz 3: `>80%` → çok yavaş şarj, neredeyse “tail” bölgesi.

- **`ChargeResult`**
  - `duration_minutes`
  - `energy_added_kwh`
  - `avg_power_kw`
  - `weather_factor`
  - `start_soc`, `target_soc`.

- **`WeatherImpact.get_temperature_factor`**
  - Düşük sıcaklıkta şarj yavaşlar, sıcaklık arttığında da termal throttling.
  - Bu faktör şarj süresi ve ortalama güce çarpan olarak giriyor.

- **`ChargingCurve.calculate_charge_time(start_soc, target_soc, peak_power_kw, temperature_c)`**
  - SOC aralığını fazlara bölüp, her faz için:
    - Etkin güç = `peak_power * temperature_factor * faz_katsayısı`
    - Enerji = `(target_soc - start_soc) / 100 * battery_kwh`
    - Süre = `energy / power`.
  - Sonuçlar toplanıp `ChargeResult` dönüyor.
  - `lru_cache` ile belli konfigürasyonların sonucu cache’lenmiş.

- **`apply_high_soc_penalty(target_soc)`**
  - Planner optimizasyonunda, %80 üzeri şarj için ceza:
    - `extra_soc = target_soc - 80`
    - `penalty_minutes = extra_soc * HIGH_SOC_PENALTY_FACTOR`.

> Burada da **piecewise deterministic model** var; gerçek DC şarj eğrisine yaklaşan kural tabanlı fonksiyon.

---

## 4. Route Planner (`app/route_planner.py`)

### 4.1. Genel Flow (dosya içi docstring’e göre)

1. **Route Selector** – En iyi Google rotasını seç.
2. **Google Elevation** – Rakım verisi çek.
3. **Route Segmenter** – Polyline’ı sabit uzunluklu segmentlere böl.
4. **Main Calculator** – Her segment için tüketim.
5. **SOC Simulator** – SOC simülasyonu + Hotspot tespiti.
6. **Station Finder** – Hotspot’lar için uygun istasyonları bul.

### 4.2. Kullanılan bileşenler

- `find_best_route` (`route_selector`)
- `create_route_segments` (`route_segmenter`)
- `calculate_route_consumption` (`consumption_engine.main_calculator`)
- `SOCSimulator`, `ChargePlanOptimizer` (`soc_simulator`)
- `calculate_charge_time` (`charging_model`)
- `WeatherService` (`services/weather_service`)
- `google_maps` (`services/google_service`)
- `calculate_co2_savings` (`sustainability_calculator`)
- `get_vehicle_model` (`vehicle_models`)

### 4.3. Hesaplama/Optimizasyon Mantığı

- Girdi: `RouteRequest`
  - Varsayılanlar:
    - Yolcu/yük (`_resolve_defaults`)
    - SOC aralıkları:
      - `MIN_SOC_RANGE = (15, 25)`
      - `TARGET_SOC_RANGE = (75, 95)`
      - `ARRIVAL_SOC_RANGE = (10, 25)`

- Adımlar:
  - **Rota seçimi:** `find_best_route` → Google Directions alternatiflerinden tek rota, distance/duration/consumption kıyaslı.
  - **Polyline + Elevation:** Google Elevation kullanıp toplam gain/loss hesaplanıyor.
  - **Segmentasyon:** `RouteSegmenter` ile polyline → segment listesi (mesafe, cumulative_distance, elevation_gain/loss).
  - **Tüketim:** `calculate_route_consumption` + araç modeli + hava durumu + yolcu/yük → her segment için `ConsumptionResult`.
  - **SOC Simülasyonu ve Hotspotlar:** `SOCSimulator.simulate` + `ChargePlanOptimizer`:
    - Başlangıç SOC, hedef varış SOC, min şarj eşiği, hedef şarj SOC aralığı.
    - Segment tüketimleri üzerinden SOC düşüşü simüle edilir.
    - SOC belirli eşiğin altına inecekse noktalar **hotspot** olarak işaretlenir.
    - Çeşitli `target_soc` kombinasyonları denenip plan skoru minimize edilir (durak sayısı + şarj süresi + kısa aralık penaltıları).
  - **İstasyon bulma:** Hotspot noktaları için `StationFinder` kullanılır (artık `SOCSimulator` içinden çağrılıyor).
  - **Şarj süreleri:** Her hotspot’ta, seçilen istasyonda:
    - `calculate_charge_time(start_soc, target_soc, peak_power_kw, temperature)` ile gerçekçi şarj süresi.
  - **CO2 hesabı:** `calculate_co2_savings(distance_km, ev_consumption_kwh, region)`.

> Route planner, **kombine bir optimizasyon** yapıyor:
> - Rota seçimi (popular vs efficient).
> - Şarj planı optimizasyonu (ChargePlanOptimizer skor fonksiyonu).
> - Şarj süreleri (charging_model).
> - Enerji tüketimi (consumption_engine).

---

## 5 .Route Selector (`app/route_selector.py`)

- Google Directions’tan **alternatif rotaları** alır (services/google_service).
- Her rota için:
  - Mesafe, süre çıkarılır.
  - “Popüler” (en kısa süre) vs “en verimli” (en kısa mesafe) rotayı karşılaştırır.
- **Dinamik eşik mantığı:**
  - `battery_kwh = vehicle.battery_capacity_kwh`
  - `distance_diff_km = |d_popular - d_efficient|`
  - Ortalama tüketim varsayımı: `avg_consumption_per_km = 0.18 kWh/km`.
  - `consumption_diff_kwh = distance_diff_km * 0.18`
  - Eşik: `threshold_kwh = battery_kwh * threshold_percent` (config üzerinden).
  - Eğer `consumption_diff_kwh < threshold_kwh` → popüler rota seç.
  - Aksi halde → en verimli rota seç.

> Yaklaşım: **enerji maliyetine göre popüler/verimli trade-off**; basit ama domain bilgisi ile kalibre.

---

## 6. Route Segmenter (`app/route_segmenter.py`)

- Polyline decode + mesafe hesapları + elevation dağıtımı.

- **`decode_polyline`**:
  - Google encoded polyline → `(lat, lon)` listesi.

- **`RouteSegmenter.create_segments(polyline, total_elevation_gain_m, total_elevation_loss_m, segment_length_km)`**
  - Polyline’daki noktalar arasında mesafe hesaplayarak toplam mesafeyi küçük parçalara böler (default 10 km).
  - Elevation gain/loss toplamını segmentlere paylaştırır (lineer dağıtım).
  - Her segment:
    - `index`
    - `start_point`, `end_point` (`GeoPoint`)
    - `distance_km`
    - `cumulative_distance_km`
    - `elevation_gain_m`, `elevation_loss_m`.

> Teknik olarak: **geometrik segmentasyon + lineer interpolation**; consumption engine bu segmentler üzerinde çalışır.

---

## 7. SOC Simulator ve ChargePlanOptimizer (`app/soc_simulator.py`)

### 7.1. SOCSimulator

- Segment tüketimlerini kullanarak:
  - Başlangıç SOC’tan başlayıp her segmentte `[consumption_kwh / battery_kwh * 100]` kadar SOC düşürür.
  - **Eşikler**:
    - `SAFETY_BUFFER_PERCENT`, `MIN_CHARGE_THRESHOLD_PERCENT`, `MIN_DISTANCE_BETWEEN_STOPS_KM`.
  - SOC kritik seviyeye yaklaştığında noktayı **ChargeHotspot** olarak işaretler.

### 7.2. ChargePlanOptimizer

- Amaç: Durak sayısını ve toplam süreyi minimize eden bir plan bulmak.
- Farklı `target_soc` değerleri deneniyor:
  - `TARGET_SOC_MIN = 65`, `MAX = 95`, `STEP = 5` (%65, 70, 75, …, 95).
- Her senaryo için:
  - Simülasyon sonucu (hotspot listesi, SOC profili).
  - **Skor fonksiyonu** (`_calculate_plan_score`):
    - **Durak penaltisi**: `num_stops * STOP_PENALTY_MINUTES` (örn. 60 dk/durak).
    - **Toplam şarj süresi**:
      - Her hotspot için:
        - `calculate_charge_time(start_soc, target_soc, battery_kwh, avg_charger_power)` ile süre tahmini.
        - En az 5 dakika clamp.
    - **Kısa aralık penaltisi**:
      - Hotspotlar arası mesafe + ortalama hızdan ≈ sürüş süresi.
      - 75 dakikadan kısa aralıklar için oranlı ceza: aralık ne kadar kısa ise o kadar fazla.

> Bu kısım, net olarak bir **heuristic optimization**: sürekli alanı (target SOC aralığı) tarayıp **score** minimize eden planı seçiyor.

#### 7.3. `ChargePlanOptimizer._calculate_plan_score` – Matematiksel Bakış

Bu fonksiyon, **her bir şarj planı** için tek bir scalar skor üretir. Amaç, bu skoru minimize etmektir.

Basitleştirilmiş haliyle skor şu bileşenlerden oluşur:

- **Durak sayısı penaltisi**  \(S_{stops}\)
- **Toplam şarj süresi**  \(S_{charge}\)
- **Kısa sürüş aralığı penaltisi**  \(S_{short}\)

Toplam skor:

\[
\text{score} = S_{stops} + S_{charge} + S_{short}
\]

- **Durak sayısı penaltisi**
  - \(n\) = hotspot sayısı (şarj durak sayısı).
  - Sabit ceza: \(C_{stop}\) (örn. 60 dakika).
  - \(S_{stops} = n \cdot C_{stop}\)
  - Yorum: Fazladan her durak için “park et, bekle, çık” gibi overhead süreleri modele katıyor.

- **Toplam şarj süresi**
  - Her durak için, `calculate_charge_time` ile elde edilen süre \(t_i\).
  - Minimum 5 dakika clamp: \(t_i' = \max(5, t_i)\).
  - \(S_{charge} = \sum_i t_i'\)
  - Yorum: Bu bileşen, yüksek hedef SOC’leri (örneğin %95) otomatik olarak pahalı yapar; çünkü şarj süresi eğrisi 80–100 arasında çok yavaştır.

- **Kısa sürüş aralığı penaltisi**
  - İki durak arası mesafe: \(d_i\) (km).
  - Ortalama hız: \(v\) (km/s).
  - Sürüş süresi: \(\Delta t_i = 60 \cdot d_i / v\) (dakika).
  - Eşik: \(T_{min}\) (örneğin 75 dakika).
  - Eğer \(\Delta t_i < T_{min}\) ise penaltı uygula:
    - Örneğin: \(p_i = k \cdot (T_{min} - \Delta t_i)\) (kodda bu mantık bir katsayı üzerinden implemente ediliyor).
  - \(S_{short} = \sum_i p_i\)
  - Yorum: 30–40 dakikada bir şarj molası verilmesini engellemek için, çok sık durakları cezalandırır.

Bu üç bileşen sayesinde skor, aşağıdaki tarz planları **doğal olarak** tercih eder:

- Durak sayısı az,
- Toplam şarj süresi makul,
- Sürüş aralıkları insan için konforlu uzunlukta.

##### ML Odaklı Genişletme Fikri

Bu skor fonksiyonu şu anda elle tasarlanmış bir **reward fonksiyonu** gibi düşünülebilir. ML ile şu genişletmeler yapılabilir:

- **Öğrenilebilir ağırlıklar**
  - \(\alpha, \beta, \gamma\) ağırlıkları ile:
  - \(\text{score} = \alpha S_{stops} + \beta S_{charge} + \gamma S_{short}\)
  - Kullanıcı geri bildirimleri (rating, “bu planı beğendim/beğenmedim”) üzerinden bu ağırlıklar öğrenilebilir.

- **Policy Learning (RL veya Bandit)**
  - Durum: (rota uzunluğu, hava durumu, araç tipi, başlangıç SOC, önceki duraklar).
  - Aksiyon: `target_soc` seçimi, belki istasyon seçiminde önceliklendirme.
  - Reward: Ters işaretli `score` (daha düşük skor → daha yüksek reward).
  - Offline loglardan (geçmiş rotalar + kullanıcı davranışı) policy öğrenilebilir.

- **Kullanıcı kişiselleştirme**
  - Bazı kullanıcılar “durak sayısı az olsun, duraklar uzun olabilir”,
    bazıları “daha sık ama kısa molalar” isteyebilir.
  - Kullanıcı profiline göre \(\alpha, \beta, \gamma\) kişiselleştirilerek **kişiselleştirilmiş rota planlama** sağlanabilir.

###### Log Tabanlı Feature Engineering + Basit ML Pipeline Taslağı

Eğer bu skoru tamamen kural tabanlı yazmak yerine **veriden öğrenmek** istersek, kabaca şu adımlar izlenebilir:

1. **Log Toplama**
   - Her gerçek rota için şu bilgileri log’la:
     - Rota özellikleri: toplam mesafe, toplam süre, elevation, hava durumu özetleri.
     - Plan özellikleri: durak sayısı, her durakta hedef SOC, toplam şarj süresi, kısa aralık sayısı.
     - Kullanıcı davranışı: planı kabul etti mi? Yeniden hesaplama istedi mi? Yolculuk sonrası rating?
     - Gerçekleşen metrikler: gerçek toplam süre, gerçek şarj süreleri (varsa telemetri).

2. **Feature Engineering**
   - Örnek feature set:
     - \(x_1\): durak sayısı (n).
     - \(x_2\): toplam şarj süresi (dakika).
     - \(x_3\): kısa aralık sayısı (\(< T_{min}\)).
     - \(x_4\): ortalama sürüş aralığı süresi.
     - \(x_5\): kullanıcı tipi (örn. "aile", "iş seyahati" gibi kategorik embedding).
     - \(x_6\): hava durumu şiddeti, vs.
   - Hedef (label):
     - Örneğin `y =` kullanıcı memnuniyeti skoru, ya da `y =` (gerçek toplam süre + ceza terimleri).

3. **Model Seçimi**
   - Basit bir **regression** modeli:
     - Linear Regression, Gradient Boosted Trees, Random Forest vs.
   - Model, `score_pred = f(x)` tahmin eder.
   - ChargePlanOptimizer, mevcut heuristic skor yerine bu `score_pred`’i minimize etmeye çalışabilir.

4. **Inference Zamanında Kullanım**
   - Her candidate plan için feature vector `x` oluştur.
   - ML modeli ile `score_pred = f(x)` hesapla.
   - En düşük `score_pred`’li planı seç.

5. **A/B Test ve Online Öğrenme**
   - Bir kısım kullanıcıda heuristic skor, bir kısımda ML skoru kullan.
   - Hangi yaklaşımın daha az iptal, daha yüksek memnuniyet ürettiğini ölç.
   - Gerekirse bandit/RL ile policy’yi zamanla güncelle.

Bu yapı sayesinde mevcut kural tabanlı skor fonksiyonu, ileride **veriyle beslenen bir öğrenilmiş skor fonksiyonuna** evrilebilir; yine de şu anki dokümantasyon, hem heuristik hem de ML yaklaşımı anlamak için yeterli bağlamı sağlar.

---

## 8. Station Finder (`app/station_finder.py`)

İstasyon arama ve seçimi için zengin bir modül.

### 8.1. Servis Entegrasyonları

- **OCM (Open Charge Map)** → `ocm_service`
  - `get_nearby_stations(lat, lon, radius, max_results, min_power_kw)` → `StationInfo` listesi.
- **Google Maps** → `google_maps`
  - Bazı mesafe / süre hesapları ve rota sapma süresi için.
- **WeatherService** → `weather_service`
  - İstasyon çevresindeki hava durumunu almak için kullanılabiliyor.

### 8.2. Filtreleme ve Seçim Mantığı

- **Filtreler:**
  - Operasyonel istasyonlar (`StatusType.IsOperational`).
  - **Connector uyumu**: `vehicle.connector_type` ile eşleşen bağlantılar (`_is_connector_compatible`).
  - Güç eşiği:
    - DC / HPC: `DC_POWER_THRESHOLD_KW`, `MIN_DC_POWER_KW`.
  - Mesafe:
    - Haversine mesafesi (`haversine_km`) ile `MAX_HAVERSINE_DISTANCE_KM` içinde kalanlar.
  - Koridor bazlı arama:
    - **CorridorSearcher**: rota koridoru boyunca, belirli genişlikte istasyonları tarıyor.

- **Skorlama:**
  - Değişkenler:
    - Yol sapma süresi (deviation).
    - Güç (kW).
    - Rating (kullanıcı puanları).
  - Ağırlıklar:
    - `WEIGHT_DEVIATION`, `WEIGHT_POWER`, `WEIGHT_RATING`.
    - Greedy seçim için: `GREEDY_WEIGHT_POWER`, `GREEDY_WEIGHT_DEVIATION`, `GREEDY_WEIGHT_RATING`.
  - Skor fonksiyonu, yüksek güç + düşük sapma + iyi rating kombinasyonunu ödüllendiriyor.

- **`find_stations_for_hotspots(hotspots, ...)`**
  - Her hotspot için, koridor araması yapıyor.
  - Her hotspot’a en fazla `MAX_STATIONS_PER_HOTSPOT` istasyon döndürüyor.
  - Planner, şarj planını bu istasyonlar arasından seçiyor.

> Station finder, **coğrafi filtreleme + greedy skorlama** kombinasyonu kullanıyor.

---

## 9. Services Katmanı

### 9.1. Google Service (`app/services/google_service.py`)

- **`GoogleMapsService`**
  - `get_directions(start: GeoPoint, end: GeoPoint) -> List[DriveLeg]`
  - `get_elevation(polyline)` – rakım datası.
  - `get_distance_matrix(...)`
  - `get_place_details(place_id)`
  - `geocode(address) -> GeoPoint`
- `@cacheable` decorator ile sonuçlar cache’leniyor (performans/limit için).
- Tek instance: `google_maps`.

### 9.2. OCM Service (`app/services/ocm_service.py`)

- **`OCMService.get_nearby_stations`**
  - Raw JSON → `StationInfo`, `ConnectorInfo`.
  - Connector türü mapping:
    - `ConnectionTypeID` → `PlugType` (Type 2, CCS2, CHAdeMO, Tesla).
  - Güç tahmini:
    - `PowerKW` yoksa `Voltage * Amps / 1000`.
  - Şarj tipi:
    - `ChargerType.AC`, `DC`, `HPC` (>=150 kW).
- Tek instance: `ocm_service`.

### 9.3. Weather Service (`app/services/weather_service.py`)

- **OpenWeatherMap**:
  - `get_current_weather(lat, lon) -> WeatherInfo`
  - `get_forecast_for_point(lat, lon) -> dict` (ileride ML için veri seti üretimi).
- **CONDITION_MAP**:
  - OWM condition ID → `WeatherCondition` (RAIN, SNOW, FOG, WINDY, CLEAR, CLOUDY).
- Basit **yağış olasılığı heuristiği**:
  - Yağmur/kar: `precip_prob = 0.8`
  - Bulutlu/sis: `0.2`
  - Açık: `0.0`.

---

## 10. Kullanılan Hesaplama Teknikleri (Özet)

- **Fizik Seviye:**
  - Potansiyel enerji (mgh) ile elevation etkisi.
  - Batarya kapasitesi/SOC ↔ kWh dönüşümleri.
  - Şarj eğrisi için faz bazlı güç modeli.

- **Kural Tabanlı Katmanlar:**
  - Sıcaklık, rüzgar, yağış çarpanları.
  - Ek kütleye bağlı tüketim artışı.
  - Hava sıcaklığına bağlı şarj hızı çarpanı.
  - POP vs ekonomik rota seçimi için basit enerji farkı hesabı.

- **Optimizasyon / Heuristic:**
  - Route Selector’de dinamik eşik analizi (energy diff vs battery capacity).
  - SOC Simulator + ChargePlanOptimizer:
    - Farklı hedef SOC değerlerini tarayıp durak sayısı + şarj süresi + kısa aralık penaltısından oluşan skor fonksiyonunu minimize etme.
  - Station Finder’de greedy istasyon seçimi (güç, sapma süresi, rating ağırlıklandırması).

- **Servis / Veri Katmanı:**
  - Google Directions + Elevation + DistanceMatrix + Places.
  - Open Charge Map.
  - OpenWeatherMap.
  - Hepsi `BaseService` + `cacheable` ile soyutlanmış.

---

# Zihin Haritası (Metin Tabanlı)

```text
EV Route Optimizer API
├── API Katmanı
│   ├── main.py (FastAPI)
│   │   ├── /optimize_route → plan_route (route_planner)
│   │   ├── /health, /test, /debug
│   │   └── Vehicle validation, geocode endpoint
│   └── models.py
│       ├── Temel enumlar: ChargerType, PlugType, WeatherCondition, AmenityType
│       ├── GeoPoint, WeatherInfo, DriveLeg, ChargeLeg
│       ├── StationInfo, ConnectorInfo, StationAmenity
│       └── RouteResponse, MultiStopRouteResponse
│
├── Route Planner (route_planner.py)
│   ├── Girdi: RouteRequest + vehicle_id + SOC aralıkları
│   ├── Adımlar:
│   │   1) Route Selector → en iyi Google route
│   │   2) Google Elevation → toplam gain/loss
│   │   3) Route Segmenter → RouteSegment listesi
│   │   4) Consumption Engine → segment tüketimleri
│   │   5) SOC Simulator → hotspot tespiti
│   │   6) Station Finder → hotspot için istasyonlar
│   │   7) Charging Model → şarj süreleri
│   │   8) CO2 hesaplama → sustainability_calculator
│   └── Çıktı: MultiStopRouteResponse
│
├── Consumption Engine (consumption_engine)
│   ├── vehicle_models.py
│   │   ├── VehicleModel (curb_weight, battery_kwh, base_consumption, connector, aux_power)
│   │   └── VEHICLE_DB, get_vehicle_model
│   ├── main_calculator.py
│   │   ├── ConsumptionResult (base, load, weather, elevation, aux, regen, total)
│   │   ├── Segment bazlı tüketim hesabı
│   │   └── calculate_route_consumption (segment listesi üzerinde)
│   └── v1_rule_based
│       ├── load_layer.py → LoadEffectCalculator (ek kütle faktörü)
│       ├── elevation_layer.py → ElevationEffectCalculator (mgh + regen)
│       └── weather_layer.py → WeatherEffectCalculator (sıcaklık, rüzgar, yağış)
│
├── Route Geometry
│   └── route_segmenter.py
│       ├── decode_polyline → koordinat listesi
│       ├── RouteSegment (start, end, distance, cumulative, gain, loss)
│       └── create_route_segments → polyline + total gain/loss → segment listesi
│
├── Route Selector (route_selector.py)
│   ├── Google Directions’den alternatif rotalar
│   ├── Her rota için mesafe/süre analizi
│   ├── Popular vs Efficient rota seçimi
│   │   └── Dinamik eşik: energy_diff_kwh vs battery_kwh * threshold_percent
│   └── Çıktı: seçilen rota + polyline + mesafe/süre
│
├── SOC Simulator & Planner (soc_simulator.py)
│   ├── SOCSimulator
│   │   ├── Segment tüketimleri ile SOC simülasyonu
│   │   └── Eşik bazlı ChargeHotspot tespiti
│   └── ChargePlanOptimizer
│       ├── target_SOC 65–95% aralığında tarama
│       ├── Skor:
│       │   ├── Durak sayısı penaltisi
│       │   ├── Charging Model ile tahmini şarj süresi
│       │   └── Kısa sürüş aralığı penaltisi
│       └── En düşük skorlu plan → final şarj stratejisi
│
├── Charging Model (charging_model.py)
│   ├── ChargingCurve
│   │   ├── 3 fazlı şarj eğrisi (0–50, 50–80, >80 SOC)
│   │   └── WeatherImpact ile sıcaklık çarpanı
│   ├── calculate_charge_time → ChargeResult (süre, enerji, avg_power)
│   ├── convert_kwh_to_soc / convert_soc_to_kwh
│   ├── apply_high_soc_penalty (%80 üzeri için dk/% penaltı)
│   └── get_charge_power_at_soc (anlık güç)
│
├── Station Finder (station_finder.py)
│   ├── Girdi: ChargeHotspot listesi + araç modeli
│   ├── OCMService → istasyon verisi
│   ├── Google Maps → mesafe/sapma süresi
│   ├── WeatherService → istasyon çevresi hava bilgisi
│   ├── Filtreler:
│   │   ├── Operasyonel istasyon
│   │   ├── Connector uyumluluğu
│   │   ├── Güç eşiği (DC/HPC)
│   │   └── Haversine & koridor mesafe limitleri
│   ├── Skorlama:
│   │   ├── Güç (kW)
│   │   ├── Sapma süresi
│   │   └── Rating
│   └── Çıktı: her hotspot için en iyi birkaç istasyon
│
└── Services (services/)
    ├── google_service.py → GoogleMapsService (directions, elevation, distance matrix, places, geocode)
    ├── ocm_service.py → OCMService (Open Charge Map → StationInfo/ConnectorInfo)
    └── weather_service.py → WeatherService (OpenWeatherMap → WeatherInfo + forecast hook)

## 14. plan_route Pseudo-code (Basit Akış)

Aşağıdaki pseudo-code, `plan_route` fonksiyonunun yüksek seviyeli davranışını, Python bilmeyen birinin bile akışı görebileceği şekilde özetler:

```text
function plan_route(request):
    # 1) Araç bilgisini al
    vehicle = get_vehicle_model(request.vehicle_model_id)

    # 2) En iyi rotayı seç (Google Directions + Route Selector)
    route_result = find_best_route(origin=request.start_location,
                                   destination=request.end_location,
                                   vehicle_model_id=request.vehicle_model_id,
                                   extra_load_kg=request.extra_load_kg)
    selected_route = route_result.selected_route
    polyline = route_result.polyline
    route_distance_km, route_duration_min = extract_distance_and_duration(selected_route)

    # 3) Elevation (rakım) bilgisini al
    if polyline is not empty:
        elevation_stats = google_maps.get_elevation_stats(polyline)
        elevation_gain_m = elevation_stats.gain_m
        elevation_loss_m = elevation_stats.loss_m
    else:
        elevation_gain_m = 0
        elevation_loss_m = 0

    # 4) Hava durumunu al (başlangıç ve bitiş → ortalama)
    start_weather  = weather_service.get_weather_at_point(start.lat, start.lon)
    end_weather    = weather_service.get_weather_at_point(end.lat, end.lon)
    avg_weather    = average(start_weather, end_weather)

    # 5) Varsayılan yolcu/yük değerlerini çöz
    passenger_count, child_count, extra_load_kg = _resolve_defaults(request)

    # 6) Rota segmentasyonu (geometri)
    segmenter = RouteSegmenter(segment_length_km=10)
    segments = segmenter.create_segments(polyline,
                                         total_elevation_gain_m=elevation_gain_m,
                                         total_elevation_loss_m=elevation_loss_m)

    # 7) Her segment için tüketim hesapla (Consumption Engine)
    segments_with_consumption = calculate_route_consumption(
        vehicle=vehicle,
        segments=segments,
        temperature_celsius=avg_weather.temp_c or DEFAULT_TEMPERATURE,
        wind_speed_mps=avg_weather.wind_speed_mps or 0,
        weather_condition=avg_weather.condition or "clear",
        extra_load_kg=extra_load_kg,
        passenger_count=passenger_count,
        child_count=child_count
    )
    total_consumption_kwh = sum(segment.consumption_kwh for segment in segments_with_consumption)

    # 8) SOC parametrelerini hesapla (min SOC, varış SOC)
    charge_min_soc, user_target_soc_override, arrival_soc = _calculate_base_soc_params(
        battery_kwh=vehicle.battery_capacity_kwh,
        start_soc=request.current_soc_percent,
        total_consumption_kwh=total_consumption_kwh,
        route_distance_km=route_distance_km,
        request=request
    )

    # 9) Şarj planını optimize et (kaç durak, hangi hedef SOC?)
    avg_speed_kmh = route_distance_km / route_duration_min * 60
    if user_target_soc_override is not None:
        # Kullanıcı hedef SOC vermiş → doğrudan bu hedefle simüle et
        simulator = SOCSimulator(battery_capacity_kwh=vehicle.battery_capacity_kwh,
                                 start_soc=request.current_soc_percent,
                                 target_arrival_soc=arrival_soc,
                                 charge_min_soc=charge_min_soc,
                                 charge_target_soc=user_target_soc_override)
        sim_result = simulator.simulate(segments_with_consumption, route_distance_km)
        charge_target_soc = user_target_soc_override
    else:
        # Optimizer: 65–95% hedef SOC aralığını tarar
        optimizer = ChargePlanOptimizer(battery_capacity_kwh=vehicle.battery_capacity_kwh)
        charge_target_soc, sim_result = optimizer.find_optimal_plan(
            segments_with_consumption,
            total_distance_km=route_distance_km,
            battery_capacity_kwh=vehicle.battery_capacity_kwh,
            start_soc=request.current_soc_percent,
            target_arrival_soc=arrival_soc,
            charge_min_soc=charge_min_soc,
            avg_speed_kmh=avg_speed_kmh
        )

    # 10) Hotspotlar için uygun istasyonları bul
    hotspots = sim_result.hotspots
    if hotspots is not empty:
        station_results = find_stations_for_hotspots(hotspots, request.vehicle_model_id)
    else:
        station_results = []

    # 11) Drive + Charge bacaklarını oluştur
    legs = _build_multi_legs(
        start_point=start_location,
        end_point=end_location,
        total_distance_km=route_distance_km,
        total_duration_min=route_duration_min,
        segments_with_consumption=segments_with_consumption,
        start_soc=request.current_soc_percent,
        final_soc=sim_result.final_soc,
        hotspots=hotspots,
        station_results=station_results,
        polyline=polyline,
        battery_capacity_kwh=vehicle.battery_capacity_kwh,
        temperature_c=avg_weather.temp_c if avg_weather else None
    )

    # 12) CO2 tasarrufu hesapla
    co2_savings = calculate_co2_savings(route_distance_km,
                                        ev_consumption_kwh=sim_result.total_consumption_kwh,
                                        country_code="TR")

    # 13) MultiStopRouteResponse oluştur ve döndür
    return MultiStopRouteResponse(
        status="success",
        total_distance_km=route_distance_km,
        total_duration_minutes=route_duration_min,
        total_co2_savings_kg=co2_savings,
        consumption_kwh=total_consumption_kwh,
        legs=legs,
        charge_stops=number_of_charge_legs(legs),
        message=human_readable_summary(...)
    )
```

Bu pseudo-code, gerçek koddaki tüm detayları göstermez ama ana akışın **hangi sırada hangi modülleri kullandığını** hızlıca anlamak için yeterlidir.

## 15. Dosya ve Metot Kullanım Rehberi (Yeni Başlayan İçin)

Bu bölüm, projeye yeni giren birinin “şu iş için hangi dosyaya bakmalıyım?” sorusunu cevaplaması için hazırlandı.

### 15.1. API ve Giriş Noktası

- **`app/main.py`**
  - FastAPI uygulaması.
  - Rota optimizasyonu için `POST /optimize_route` → `plan_route` çağırır.
  - Sağlık kontrolleri ve test/debug endpoint’leri buradadır.
  - **Ne zaman bakmalısın?**
    - Backend API contract’ını (gelen/giden JSON) görmek istediğinde.
    - FastAPI middleware, CORS vb. ayarları değiştireceğinde.

### 15.2. Ana İş Mantığı

- **`app/route_planner.py`**
  - Tüm akışı yöneten “orkestratör”.
  - Rota seçimi, segmentasyon, tüketim hesabı, SOC simülasyonu ve istasyon bulmayı bir araya getirir.
  - Önemli fonksiyonlar:
    - `plan_route` → dış dünyadan çağrılan ana fonksiyon.
    - `_resolve_defaults` → yolcu/yük varsayılanlarını ayarlar.
    - `_calculate_base_soc_params` → min SOC, arrival SOC, user override hesaplar.
    - `_build_multi_legs` → Drive/Charge bacaklarını inşa eder.
  - **Ne zaman bakmalısın?**
    - Akışta yeni bir adım eklemek istediğinde (örneğin trafik verisi).
    - Rota planlama stratejisini değiştirmek istediğinde.

### 15.3. Tüketim Motoru (Consumption Engine)

- **`app/consumption_engine/main_calculator.py`**
  - Araç fiziği + hava durumu + yük + elevation’ı kullanarak segment başına kWh hesabı yapar.
  - `calculate_route_consumption` → route_planner’ın çağırdığı ana fonksiyon.
- **`app/consumption_engine/v1_rule_based/*`**
  - `load_layer.py` → kütle/yük etkisini hesaplar.
  - `elevation_layer.py` → m*g*h formülü ile tırmanış/iniş enerjisi.
  - `weather_layer.py` → sıcaklık, rüzgar, yağış çarpanları.
- **`app/consumption_engine/vehicle_models.py`**
  - Statik araç veri tabanı (`VEHICLE_DB`),
  - `get_vehicle_model` ile bu veriye erişim.
  - **Ne zaman bakmalısın?**
    - Araç veri tabanına yeni model eklemek istediğinde.
    - Tüketim hesabında kullanılan fizik/heuristic’i değiştirmek istediğinde.

### 15.4. Rota Geometrisi ve Segmentasyon

- **`app/route_segmenter.py`**
  - Polyline → `RouteSegment` listesi dönüşümünü yapar.
  - Hangi noktalarda segment kesileceğini (ör. her 10 km) belirler.
  - Elevation gain/loss’u segmentler arasında paylaştırır.
  - **Ne zaman bakmalısın?**
    - Segment uzunluğunu değiştirmek (10 km yerine 5 km) istediğinde.
    - Daha sofistike elevation dağıtımı/ara nokta interpolasyonu gerektiğinde.

### 15.5. Rota Seçici (Google Directions + Karar Mantığı)

- **`app/route_selector.py`**
  - Google Directions’tan gelen alternatif rotalar arasından seçim yapar.
  - Enerji farkı küçükse popüler rotayı, büyükse daha kısa olanı seçer.
  - **Ne zaman bakmalısın?**
    - Alternatif rota sayısını, seçim kriterlerini ya da eşiği değiştirmek istediğinde.

### 15.6. SOC Simülasyonu ve Şarj Planı

- **`app/soc_simulator.py`**
  - `SOCSimulator` → segment tüketimleri üzerinden SOC zaman serisini üretir, hotspot’ları bulur.
  - `ChargePlanOptimizer` → farklı `target_soc` değerlerini deneyerek en iyi şarj planını bulur.
  - **Ne zaman bakmalısın?**
    - Kullanıcının “daha az/daha çok durak” isteğine göre şarj stratejisini değiştirmek istediğinde.
    - ML tabanlı bir şarj policy’si eklemek istediğinde.

### 15.7. Şarj Eğrisi ve Şarj Süresi Hesabı

- **`app/charging_model.py`**
  - Gerçekçi 3 fazlı şarj eğrisi (0–50, 50–80, 80–100 SOC).
  - `calculate_charge_time` → belirli bir istasyonda, `start_soc → target_soc` arası süreyi ve eklenen enerjiyi verir.
  - **Ne zaman bakmalısın?**
    - Farklı araçlar için farklı şarj eğrileri modellemek istediğinde.
    - Hava sıcaklığının şarj hızına etkisini daha detaylı yapmak istediğinde.

### 15.8. İstasyon Bulma ve Filtreleme

- **`app/station_finder.py`**
  - Hotspot koordinatları etrafında OCM’den istasyon arar.
  - Bağlantı tipi, güç, mesafe ve rating’e göre istasyonları filtreler/skorlar.
  - `find_stations_for_hotspots` → planlayıcının kullandığı ana fonksiyon.
  - **Ne zaman bakmalısın?**
    - Yeni filter/score kriterleri eklemek (fiyat, marka, olanaklar) istediğinde.

### 15.9. Dış Servisler

- **`app/services/google_service.py`**
  - Google Directions, Elevation, Distance Matrix, Places, Geocoding.
- **`app/services/ocm_service.py`**
  - Open Charge Map → `StationInfo` / `ConnectorInfo` mapping.
- **`app/services/weather_service.py`**
  - OpenWeatherMap → `WeatherInfo`.
  - **Ne zaman bakmalısın?**
    - Yeni API parametreleri eklemek,
    - Cache sürelerini veya hata yönetimini değiştirmek istediğinde.
