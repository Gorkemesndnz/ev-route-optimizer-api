# CLAUDE.md — EV Route Optimizer API

Bu dosya Claude Code (ve diğer LLM yardımcıları) için projeye özel rehberdir.
Genel kurallar `~/.claude/CLAUDE.md`'de; bu dosya **bu repo'ya özel** mimari ve
son refaktör notlarını içerir.

---

## 1. Proje Özeti

FastAPI backend — multi-stop EV rota optimizasyonu. Stateless compute engine:
araç verisi (`VehicleSpec`) **.NET MSSQL backend'inden** payload olarak gelir,
bu Python servisi sadece hesap motoru olarak çalışır.

Ana modüller:
- `app/route_planning/orchestrator.py` — `plan_route()` ana akış
- `app/consumption_engine/` — Physics + ML stub motorları
- `app/soc_simulator.py` — SOC simülasyonu, hotspot tespiti
- `app/optimization/` — **Faz 2 Pareto karar mekanizması** (yeni)
- `app/services/google_service.py` — Google Maps + Roads API
- `app/routers/` — FastAPI endpoint'leri (`optimize`, `stations`, `dev`, `trips`)

---

## 2. Son Yapılan Büyük Refaktör (Faz 1 + Faz 2)

**Tarih:** 2026-04 / Sürüm: v2.1
**Test durumu:** 135/135 geçiyor (live & stress testler hariç)

### 2.1 Faz 1 — Tüketim Motoru + Roads Temizliği

**Neden?** Code-review'da 7 madde tespit edildi:

| # | Sorun | Çözüm | Etki |
|---|-------|-------|------|
| 1 | Eğim kWh → motor verim kayıpları yok sayılıyor | `DRIVETRAIN_EFFICIENCY = 0.88` constant + uphill bölme | Tüketim ~%14 daha doğru |
| 2 | `SimpleSegment` rota süresini distance/speed varsayıyor | `duration_minutes` opsiyonel parametre + Google'dan geçir | Saatlik sürüş enerjisi gerçek |
| 3 | `DEFAULT_AVG_SPEED_KMH` 50 vs 60 tutarsız | 60.0 olarak hizalandı | Determinizm |
| 4 | Roads API her çağrıda Google'a gidiyor | `@cacheable(prefix="roads_snap", ttl=3600)` | 1000 req/$10 → cache hit ile düşer |
| 5 | Roads fail olunca sessiz fallback | `_roads_fallback_stats` + `get_roads_fallback_stats()` | Operasyonel monitoring |
| 6 | `_is_station_on_route_side` ölü kod (Roads ile değiştirildi) | Sildirildi (`station_finder.py` + `filter.py`) | -70 satır karmaşıklık |
| 7 | `or 80` magic number | `DEFAULT_CHARGE_TARGET_SOC = 80.0` (constants.py) | Tek kaynak |

**Hata çıkabilecek noktalar (Faz 1):**
- `DRIVETRAIN_EFFICIENCY = 0.88` literatür ortalaması; gerçek araç verim eğrisi
  yük/hıza göre değişir. **TODO**: araç başına `drivetrain_eff` field'ı eklenebilir.
- Roads fallback istatistikleri **in-memory dict** — process restart'ta sıfırlanır.
  Prod'da Prometheus/StatsD'e push edilmeli.
- Roads cache key polyline-tabanlı; rota *çok yakın ama farklı* olduğunda
  cache miss → maliyet artabilir.

### 2.2 Faz 2 — Pareto Karar Mekanizması

**Neden?** Eski sistemde her şarj durağında "her zaman %80'e kadar şarj et"
greedy mantığı vardı. Bu:
- Time-priority kullanıcısına battery-care davranışı dayatıyordu
- Cost-priority'de gereksiz şarj süresi/maliyet üretiyordu
- Battery-care'de hızlı DC şarjı kısıtlamıyordu

**Yeni mimari:**

```
plan_route()
    │
    ▼
RouteRequest.smart_plan_enabled = True?
    │
    ├── True → _run_pareto_simulation()
    │       1. Baseline simülasyon (her stop = %80) → hotspot tespiti
    │       2. Backward induction → her stop için min_target_soc
    │       3. ParetoSolver.solve() → grid search [min_target..95] %5 step
    │          - 5+ stop varsa greedy fallback (kombinatoryel patlama önle)
    │          - J = w_t·T̂ + w_c·Ĉ + w_e·Ê + w_s·Ŝ
    │          - Hard constraint: arrival margin < 0 → J = +inf
    │       4. Final simülasyon (per_stop_targets uygulanır)
    │       5. DecisionLogger.write() → JSONL
    │
    └── False → _run_soc_simulation()  [eski greedy yol — backward compat]
```

**Yeni dosyalar:**
- `app/optimization/modes.py` — `OptimizationMode` enum + `WEIGHT_TABLE`
  - `BALANCED` (default), `TIME_PRIORITY`, `COST_PRIORITY`, `BATTERY_CARE`
- `app/optimization/dynamic_buffer.py` — leg-spesifik güvenlik tamponu
  (final ×1.5, mid ×0.7, %20 cap, hava/yük/eğim cezaları)
- `app/optimization/backward_planner.py` — geriye doğru planlama
- `app/optimization/objective.py` — J skoru hesabı
- `app/optimization/pareto_solver.py` — itertools.product grid search
- `app/optimization/decision_logger.py` — `DecisionRecord` + `OutcomeRecord`
  JSONL append, KVKK uyumlu (1km koordinat yuvarlama, env override)
- `app/routers/trips.py` — `POST /trips/{trip_id}/outcome` (geri bildirim)
  + `GET /trips/recent` (decision↔outcome JOIN)

**Hata çıkabilecek noktalar (Faz 2):**

1. **Pareto bounds kalibre edilmemiş** — `objective.py` normalize bantları
   (T: 60-600 dk, C: 50-800 TL) ilk tahmin. Prod'da ilk hafta veri ile recalibrate.
2. **`avg_price_tl_per_kwh = 8.0` sabit** — `pareto_solver.py`'da hardcoded.
   Faz 2.5'te `pricing_service` ile değiştirilecek.
3. **Greedy fallback eşiği 5 stop** — bunun üstünde Pareto **optimal değil**.
   5⁵=3125 kombinasyon eşiğinde tutuldu; 6+ stoplu uzun rotalarda çözüm
   kalitesi düşebilir (bu yine de greedy'den iyi).
4. **`per_stop_targets` SOCSimulator'da post-process atlanması** —
   `if hotspots and not user_override and per_stop_targets is None`
   koşulundaki *üç* sınır kritik. Yanlış kombinasyon eski greedy'i tekrar tetikler.
   `tests/test_phase2_pareto.py::test_socsimulator_with_per_stop_targets_overrides`
   bu rejresyonu yakalar; **silmeyin/değiştirmeyin**.
5. **Trip ID propagation** — `plan_route()` başında UUID üretilir, `_run_pareto`
   ve response'a thread edilir. Yeni endpoint eklerseniz `trip_id` taşımayı
   unutmayın; outcome backfill `trip_id` üzerinden JOIN yapar.
6. **Decision/outcome dosyaları `logs/` altına yazılır** — bu klasörün prod
   deployment'ta var olması gerekir. `.gitignore`'a eklenmeli.
7. **`logger.debug` Unicode** — Windows cp1254 environment'larda `→`/`×` gibi
   karakterler `UnicodeEncodeError` üretir. Yeni log mesajlarında ASCII (`->`, `x`) kullanın.

### 2.3 Plan B — Eski Test Tabanı Modernizasyonu

**Neden?** Faz 1+2 değişiklikleri 12 eski testi kırdı. Sebep:
- API response artık `ApiResponse[T]` wrapper'ında (`{success, data, error}`)
- Vehicle catalog endpoint'leri (`/vehicles/brands`, `/by_brand`, `/search`)
  ve `/geocode` **silindi** (.NET'e taşındı / frontend kullanıyor)
- `get_vehicle_model()` mock catalog deprecated → ValueError fırlatıyor

**Yapıldı:**
- `tests/test_api_endpoints.py` tamamen yeniden yazıldı:
  - Ölü endpoint testleri silindi (vehicles, geocode)
  - Tüm assertion'lar `body["success"]` / `body["data"]` üzerine güncellendi
  - **Bonus**: `optimization_mode` validator için yeni test eklendi
- `tests/test_engine_contract.py`:
  - `get_vehicle_model('abarth_500e_...')` çağrısı kaldırıldı
  - `_build_test_vehicle()` helper'ı `VehicleSpec`'i elle kurar

### 2.4 Refaktör Sırasında Bulunup Düzeltilen Gizli Bug

`app/main.py::validation_exception_handler` — `field_validator` raw `ValueError`
fırlattığında `RequestValidationError.errors()` içinde **serialize edilemez exception
nesnesi** döndüğü için `PydanticSerializationError` veriyordu (Pareto validator'ı
bu yola tetikledi).

**Düzeltme:** `jsonable_encoder(errors, custom_encoder={Exception: str, ValueError: str})`
ile JSON-safe hale getirildi. Artık tüm 422 cevapları `ApiResponse` şablonunda döner.

---

## 3. Test Stratejisi

| Dosya | Tip | Kapsam | Hız |
|-------|-----|--------|-----|
| `test_core_logic.py` | Unit | SOC params, fizik motoru | Hızlı |
| `test_phase1_fixes.py` | Unit | Faz 1 düzeltmeleri | Hızlı |
| `test_phase2_pareto.py` | Unit | Pareto solver, buffer, objective | Hızlı |
| `test_phase2_outcome_backfill.py` | Unit + Integration | DecisionLogger + endpoint | Hızlı |
| `test_phase2_smoke_e2e.py` | E2E | Full Pareto pipeline + outcome | Orta |
| `test_engine_contract.py` | Contract | Engine ABC subclass + return shape | Hızlı |
| `test_api_endpoints.py` | Integration | HTTP layer + ApiResponse şablonu | Hızlı |
| `test_safe_harbor_live.py` | Live | Gerçek Google API (CI'da `-k "not live"`) | Yavaş |

**Komutlar:**
```bash
# Tam suite (live hariç; manuel benchmark/stress scriptleri scripts/ altında)
pytest tests/ --ignore=tests/test_safe_harbor_live.py

# Sadece Faz 2
pytest tests/test_phase2_pareto.py tests/test_phase2_outcome_backfill.py tests/test_phase2_smoke_e2e.py -v
```

---

## 4. Hâlâ Açık Olan Teknik Borçlar

`~/.claude/projects/.../memory/project_fastapi_tech_debt.md` ile senkron tut:

1. **HTTP status code** — `optimize_route` business hatasında 200 döner (status alanında "error"). RESTful değil; v3.0'da düzeltilecek.
2. **Router prefix** — `/optimize_route` yerine `/api/v2/routes/optimize` benzeri kanonik isim.
3. **Hardcoded plug type** — `stations.py:49` `PlugType.TYPE2` sabit; gerçekte istasyondan gelmeli.
4. **Singleton DI** — `get_decision_logger()` modül-seviye singleton; FastAPI `Depends` pattern'ine geçirilebilir.
5. **Pareto pricing** — `avg_price_tl_per_kwh = 8.0` sabit (Faz 2.5'te değiştir).
6. **Pareto bounds recalibration** — prod 1 hafta verisi ile.

---

## 5. Refaktör Yaparken Dikkat Edilecekler

- **Backward compat zorunlu**: `smart_plan_enabled=False` yolu eski greedy davranışı bire bir korur.
  51 core_logic testi bunu doğrular; kaldırmayın.
- **Response model değişimi** = API contract değişimi. Frontend (.NET) ile koordinasyon gerekir.
- **DecisionRecord schema değişimi** = JSONL geriye uyum kaybı; `version` field'ı ekleyin.
- **Yeni optimization mode eklerken**: `OptimizationMode` enum + `WEIGHT_TABLE` aynı yerde; pareto testlerine bir mode-specific test ekleyin.
- **Magic number eklemeyin** — `app/constants.py`'a koyun.
- **Windows encoding**: log mesajlarında ASCII kullanın (`→`, `×` yerine `->`, `x`).

---

## 6. Hızlı Referans

- Ana dal: `main`
- Aktif worktree: `claude/vigilant-liskov-cb9b6b` (Faz 1+2 refaktörü)
- Decision log: `logs/decisions_YYYY-MM-DD.jsonl`
- Outcome log: `logs/outcomes_YYYY-MM-DD.jsonl`
- Trip ID format: 12-karakter UUID prefix (`uuid.uuid4().hex[:12]`)
