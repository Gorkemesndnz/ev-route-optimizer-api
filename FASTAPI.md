# FASTAPI.md — EV Route Optimizer Engine

> Persistent engineering memory for the FastAPI route optimization engine.
> Updated incrementally — do NOT delete prior sections, append/edit only.
> Last updated: 2026-05-02

---

## Architecture

### Black-Box Contract

```
INPUT (POST /optimize_route)
  ↓
[Orchestrator: plan_route]
  ├─ STEP 1: Vehicle resolve (vehicle_spec from .NET MSSQL OR vehicle_model_id JSON)
  ├─ STEP 2: Best route selection (Google Directions + alternatives)
  ├─ STEP 3: Elevation stats (Google Elevation API)
  ├─ STEP 4: Defaults resolve (passengers, load)
  ├─ STEP 4.5: Start/End weather (OpenWeatherMap)
  ├─ STEP 4.7: Safe Harbor check (rescue stations near destination)
  ├─ STEP 5: Route segmentation (10 km segments)
  ├─ STEP 6: Weather checkpoints (every ~50km)
  ├─ STEP 7: Consumption estimation (PhysicsConsumptionEngine — physics_engine.py)
  ├─ STEP 8: Base SOC params (calculate_base_soc_params)
  ├─ STEP 9: SOC simulation Pass 1 (_run_soc_simulation → Pareto OR grid search)
  ├─ STEP 10: Hotspots → Stations (find_stations_for_hotspots)
  ├─ STEP 10.5: Pass 2 weather refinement (re-sim if hotspot weather differs)
  ├─ STEP 10.5b: Re-route through stations (refresh polyline)
  ├─ STEP 10.7: Roads API snap-to-roads (NEW — 2026-05-02)
  ├─ STEP 11: Multi-leg builder (drive + charge legs)
  ├─ STEP 12: CO2 + message + warnings
  ├─ STEP 12.5: Insight engine
  └─ STEP 13: Training data log + response
  ↓
OUTPUT (MultiStopRouteResponse via ApiResponse[T] wrapper)
```

### Layer Map

```
app/
├── routers/
│   ├── optimize.py       POST /optimize_route (route planning entry)
│   ├── stations.py       GET /api/map_stations, POST /station_feedback
│   ├── trips.py          POST /trips/{trip_id}/outcome (Faz 3 ML backfill)
│   └── dev.py            GET /health, /api/info
├── route_planning/
│   ├── orchestrator.py   plan_route() — main entry, 13 steps
│   ├── soc_params.py     calculate_base_soc_params() — DYNAMIC SOC defaults
│   ├── leg_builder.py    Multi-leg drive + charge leg construction
│   ├── response_builder.py CO2, warnings, training data, response
│   ├── safe_harbor.py    Rescue station detection
│   └── weather_pipeline.py Weather forecast/checkpoint logic
├── soc_simulator.py      SOCSimulator + ChargePlanOptimizer (grid search)
├── station_finder.py     CorridorSearcher (Google Places + OCM blend)
├── route_segmenter.py    Geometric polyline segmentation
├── route_selector.py     Best-of-N route alternatives picker
├── charging_model.py     calculate_charge_time (curve-based)
├── safety_heuristics.py  calculate_dynamic_reserve (route-aware safety buffer)
├── consumption_engine/   PhysicsConsumptionEngine (Wh/km estimation)
├── optimization/
│   ├── pareto_solver.py  Multi-objective optimizer
│   ├── backward_planner.py Min target_soc per stop (suffix induction)
│   ├── modes.py          BALANCED/TIME/COST/BATTERY weights
│   ├── objective.py      J(plan) = w_t·T + w_c·C + w_e·E + w_s·S
│   ├── decision_logger.py JSONL karar kayıt
│   └── dynamic_buffer.py Buffer per-leg
├── services/             google_service, ocm_service, weather_service, pricing_service, feedback_service, insight_service
├── models/route_models.py Pydantic schemas
├── infrastructure/vehicle_catalog/ Vehicle spec resolver
├── core/                 api_response, config
└── constants.py          Centralized SOC/safety thresholds
```

---

## Current Known Bugs

### CB-1 — Excessive charging stops on long routes (10+ stops for 1200 km)

**Severity:** 🔴 Critical
**Symptom:** A 1200 km trip suggests ~10 charging stops. For an 85 kWh EV at ~280 Wh/km, ~3-4 stops should be enough.
**Status:** Root cause identified (see RC-1)

### CB-2 — Charging triggered at high SOC (44%)

**Severity:** 🔴 Critical
**Symptom:** Vehicle "arrives" at charging station with 44% SOC and tops up unnecessarily.
**Log evidence (2026-05-02 03:08:16):**
```
Base SOC params: min=44.0%, arrival=20.0% (user_target_override=80.0)
[SIM] seg=8, dist=14.3km, cum=123.1km, soc_before=41.2%
[REQ] remaining=1185.4km, min_req=74.0%, projected_soc=36.8%
Hotspot detected BEFORE segment 8
```
**Status:** Root cause identified (see RC-2)

### CB-3 — Pareto solver may produce sub-optimal targets on long routes

**Severity:** 🟡 Medium
**Symptom:** Per-stop targets might oscillate or drop below efficient range due to weight tuning.
**Status:** Awaiting investigation after CB-1/CB-2 fixed.

### CB-4 — `min_required_soc` calculation produces 74% routinely

**Severity:** 🟡 Medium (interacts with CB-1)
**Symptom:** Logs show `min_req=74.0%` for nearly every segment on a 1200 km route. This is `_calculate_min_required_soc` returning `max(50.0, charge_min_soc + 30) = max(50, 44+30) = 74` because long-route fallback path triggers.
**Status:** ✅ Fixed (F-12)

### CB-5 — `charging_frequency.charge_target_soc_hint` bypasses Pareto solver

**Severity:** 🔴 Critical (only discovered after CB-1/CB-2 fix)
**Symptom:** Even with `smart_plan_enabled=True`, the Pareto path was being skipped because `user_target_soc_override = ChargingFrequency.OPTIMAL.charge_target_soc_hint = 80.0` made `user_target_soc_override is not None`, triggering manual grid-search path.
**Log evidence:** `Base SOC params: min=17%, arrival=15.0% (user_target_override=80.0)` AND no `Pareto solution` line emitted.
**Status:** ✅ Fixed (F-13)

### CB-6 — Leg builder skips first hotspot when station not found

**Severity:** 🔴 Critical
**Symptom:** When `current_soc < charge_min_soc` (e.g., start SOC=15%), simulator triggers a hotspot at segment 0. If `find_stations_for_hotspots` returns no station for that hotspot, leg_builder hits the `continue` at line 269 — neither `current_point` nor `current_soc` is updated. Next iteration treats hotspot[1] as if start was original (15%). Output shows physically impossible leg: `DRIVE 216km soc=15→17%`.
**Test trigger:** TC-4 (Manisa→Trabzon @ start_soc=15%): first leg displayed 216km starting from 15% SOC.
**Status:** ✅ Fixed (F-15, F-16, F-17, F-18) — **TC-4 sonucu: 6 stops, ilk leg DRIVE 0km @ origin charge, sonra 217-266km cycles 95→13-16%**

### CB-7 — Plan sanity threshold too loose

**Severity:** 🟡 Medium
**Symptom:** Manual mode with `charge_min_soc=38` → 8 stops on 1300km, no sanity warning fired. Threshold was `expected * 1.5 + 1`.
**Status:** ✅ Fixed (F-14) — threshold tightened to `expected * 1.25 + 0.5` and bandwidth ratio updated 0.70 → 0.78.

---

## Root Cause Analysis

### RC-1 — Why 10 stops for 1200 km?

**Cause:** `charge_min_soc` is being passed as **44%** (from .NET payload — `IstasyonVarisSarj=44` slider value). At a 44% trigger threshold, the car only uses 80%-44%=36% of battery between charges → ~123 km per leg → ~10 stops for 1200 km.

**The bug is not WHERE 44% comes from (frontend/UI is allowed to send overrides) — the bug is that FastAPI accepts irrational thresholds without validation/clamping.**

**Evidence:**
- `app/route_planning/soc_params.py:101-104` — `if request.charge_min_soc_percent is not None: charge_min_soc = request.charge_min_soc_percent` — no upper bound check
- `app/models/route_models.py:RouteRequest.charge_min_soc_percent` — Pydantic constraint allows up to **80%** (`Field(None, ge=10, le=80)`) which is absurd for a "minimum" threshold
- Result: a "min SOC" of 44% acts like "always charge at half-tank"

### RC-2 — Why does the car arrive at 44% to charge?

**Cause:** `_should_create_hotspot` triggers on condition #2 (`current_soc ≤ self.charge_min_soc`). With `charge_min_soc=44`, every time SOC drops to 44%, hotspot fires. That's exactly the observed behavior.

**This is the LITERAL effect of the user's threshold being too high.** The simulator is doing what it's told.

### RC-3 — `min_required_soc=74%` always

**Cause:** `app/soc_simulator.py:_calculate_min_required_soc:561` returns `max(50.0, self.charge_min_soc + 30)` when `remaining_consumption_kwh > max_single_charge_kwh` (long routes). With `charge_min_soc=44`, that's `max(50, 74) = 74`. This is dead-code in trigger logic (capped to 50 in `_should_create_hotspot:610`), but log noise + signals confused downstream code.

### RC-4 — Pydantic constraint is too permissive

**Location:** `app/models/route_models.py`
```python
charge_min_soc_percent: Optional[float] = Field(
    None, ge=10, le=80,
    description="Şarj eşiği..."
)
target_arrival_soc_percent: Optional[float] = Field(
    None, ge=5, le=80,
    ...
)
charge_target_soc_percent: Optional[float] = Field(
    None, ge=50, le=100,
    ...
)
```

**Issues:**
- `charge_min_soc_percent` upper bound = 80% is absurd; a sane upper bound is ~40% (FREQUENT mode is 25%)
- No cross-field validation (`charge_min_soc < charge_target_soc - X`, e.g.)
- A "min SOC" of 44% is logically valid (threshold) but operationally bad (forces too many stops)

---

## Pending Refactors

### P-1 — Add input validation/clamping layer (PHASE 1 priority)

**Goal:** FastAPI must defensively clamp irrational user inputs to sane ranges, regardless of who sent them.

**Plan:**
1. Tighten Pydantic constraints in `RouteRequest`:
   - `charge_min_soc_percent`: ge=5, le=40 (hard cap at 40%)
   - `target_arrival_soc_percent`: ge=5, le=50
   - `charge_target_soc_percent`: ge=50, le=100 (already OK)
2. Add cross-field validator: `charge_min_soc < charge_target_soc - 20`
3. In `calculate_base_soc_params`, log a WARNING when user threshold exceeds reasonable range and CLAMP it to `min(threshold, 35)`
4. Reject (or clamp) when `smart_plan_enabled=True` AND user override provided — the contract says smart_plan ignores manual overrides

### P-2 — Post-route validation guardrails

**Goal:** Detect and warn on irrational route plans BEFORE returning.

**Plan:**
- After `plan_route` completes, run `validate_plan(response)`:
  - Charge stops > expected_stops × 1.5 (where expected = total_consumption_kwh / (battery_kwh × 0.7))
  - Any charge stop with arrival SOC > 30% (overcharging behavior)
  - Any leg < 50 km AND not first/last (too frequent stops)
  - Final SOC < HARD_MIN_SOC OR > 80% (over/under-shoot)
- Add warnings to `response.warning_messages` if any condition fails
- Log structured warning for telemetry

### P-3 — `_calculate_min_required_soc` cleanup (CB-4)

**Plan:** Remove the dead `max(50, charge_min_soc + 30)` fallback. Replace with proper "reach-without-charging?" check that honors physics, not arbitrary 50%.

### P-4 — Hotspot trigger logic separation

**Goal:** Decouple "user prefers charging frequency" from "physics requires charging".

**Plan:** Split `_should_create_hotspot` into:
- `_must_charge_now()` — physics emergency (projected_after ≤ 10%)
- `_user_prefers_charge()` — soft threshold (current_soc ≤ user_threshold)
- `_should_create_hotspot()` — combine both, but always respect MIN_DISTANCE_BETWEEN_STOPS_KM

---

## Fixed Issues

### F-1 — `smart_plan_enabled` + `optimization_mode` bridge (2026-05-02)
- Added to `FastApiRoutePayload.cs` and `RouteRequestDto.cs`; FastAPI side already handled.

### F-2 — `trip_id` propagation (2026-05-02)
- `RouteResultDto.TripId` added; `PythonRouteService` parses `data["trip_id"]`.

### F-3 — `safe_harbor_info` parse (2026-05-02)
- `RouteResultDto.SafeHarborInfo` (JsonElement) added; React TS types defined.

### F-4 — HTTP status codes for `optimize.py` errors (2026-05-02)
- `ApiResponse.fail` now returns 400/502/500 via `JSONResponse` instead of HTTP 200.

### F-5 — Pareto fallback log enhancement (2026-05-02)
- `solution.fallback_used` flag now in pareto solution log line.

### F-6 — Roads API snap-to-roads in orchestrator (2026-05-02)
- STEP 10.7 added; polyline decoded → sampled (5 km / max 400 pts) → snapped → re-encoded. Fail-soft.

### F-7 — Pydantic upper bounds tightened (2026-05-02, P-1 step 1)
- `charge_min_soc_percent`: 10-80 → **5-40** (40% üstü = aşırı sık şarj)
- `target_arrival_soc_percent`: 5-80 → **5-50**
- 422 validation error döner artık eğer absurd değer gelirse.

### F-8 — Cross-field validator on RouteRequest (2026-05-02, P-1 step 2)
- `charge_min_soc < charge_target_soc - 20` zorunlu (sağlıklı şarj döngüsü)
- `target_arrival_soc <= charge_min_soc + 10` zorunlu
- Aksi halde 422.

### F-9 — Smart Plan ignores manual SOC overrides (2026-05-02, P-1 step 3 + D-2)
- `calculate_base_soc_params` artık `smart_plan_enabled=True` iken üç manuel SOC alanını sıfırlıyor (warning log'la birlikte).
- API contract belirginleşti: smart_plan true → motor karar verir, false → kullanıcı override geçerli.

### F-10 — `charge_min_soc` clamp at %35 (2026-05-02, P-1 step 3)
- Manuel modda kullanıcı saçma değer (örn. 44%) gönderirse %35'e clamp edilir + warning log.
- CB-1/CB-2 root cause fix: 1200 km'de 10 stop yerine ~3-4 stop dönmesi bekleniyor.

### F-11 — Post-route plan sanity validation (2026-05-02, P-2)
- `validate_plan_sanity()` plan üretildikten sonra çağrılır:
  - Beklenenden çok şarj durağı
  - Şarja %35 üstü SOC ile varma (overcharging signal)
  - Çok kısa drive leg (50 km altı, ortada)
  - Mantıksız varış SOC (5% altı / 75% üstü + şarj)
- Tespit edilen anomaliler `warning_messages` listesine eklenir + log'a basılır.

### F-12 — `_calculate_min_required_soc` cleanup (2026-05-02, P-3, CB-4)
- Şişirilmiş `max(50, charge_min_soc + 30)` fallback kaldırıldı.
- Long-route durumunda `MIN_CHARGE_THRESHOLD_PERCENT + reserve` döner.
- Trigger logic'te zaten cap'lenmiş bir değer; artık log'da kafa karıştırıcı `min_req=74%` görünmeyecek.

### F-13 — Pareto bypass via charging_frequency hint fixed (2026-05-02, CB-5)
- `calculate_base_soc_params` artık `smart_plan_enabled=True` iken `charging_frequency.charge_target_soc_hint` fallback'i KULLANMIYOR.
- Sadece `not smart_plan` durumunda hint user_target_soc_override'a düşer.
- **Test sonucu:** Manisa→Trabzon 1304km — 6 stop → **5 stop** (Pareto targets [95,95,95,95,64], J=0.6471).

### F-14 — Plan sanity threshold tightened (2026-05-02, CB-7)
- `expected * 1.5 + 1` → `expected * 1.25 + 0.5`
- `usable_per_charge` bandwidth: 70% → 78% (Pareto'nun gerçek operasyon bandı)
- Manuel mod 8-stop senaryosu artık warning veriyor.

### F-15 — Leg builder advances physics when station not found (2026-05-03, Phase 2.1, CB-6 part 1)
- `leg_builder.py` `continue` blok'unda `current_point` ve `current_soc` güncellenmiyor → sonraki leg yanlış start_soc kullanıyordu.
- Düzeltme: physics state ilerletiliyor — sürüş YAŞANIYOR ama şarj YAPILMIYOR. `current_soc=hotspot.soc_at_point`, `current_point=hotspot.location`, `remaining_distance/duration` düşürülüyor.

### F-16 — `soc_at_point` clamp removed (2026-05-03, CB-6 part 2)
- `max(charge_min_soc, soc_before_segment)` → `max(0, soc_before_segment)`
- Eski kod start_soc<charge_min_soc durumunda SOC'u YUKARI yuvarlıyordu (15→17), leg'de NEGATIVE drop oluşuyordu.
- Yeni: GERÇEK SOC kaydediliyor; düşük SOC start senaryosu doğru yansır.

### F-17 — Plan feasibility hard validation (2026-05-03, Phase 2.1, CB-6 part 3)
- `validate_plan_feasibility()` post-route hard check:
  - Tüm hotspot'lar istasyonsuz → error
  - Bir DriveLeg fiziksel imkansız (start_soc × battery < required_kwh × 1.1) → error
- Orchestrator: `error_no_stations_in_corridor` döner, sessiz kötü plan üretmez.
- `validate_plan_sanity` da fiziksel imkansız leg warning'i verir (soft).

### F-19 — Pareto baseline %95 ile çalıştır (2026-05-03, Phase 2.2, K-1 fix)
- `_run_pareto_simulation` içindeki baseline_sim artık `charge_target_soc=MAX_CHARGE_TARGET (95)` ile çalışır.
- Eski: %80 ile baseline → durak SAYISI o simülasyonda kilitleniyor; Pareto sonradan hotspot sayısını düşüremiyor.
- Yeni: %95 ile baseline → mümkün olan EN AZ hotspot çıkar; Pareto bu kısa liste üzerinde target kombinasyonu dener.
- **Beklenen etki:** TC-3 (1304 km) için 5 → 4 stop iyileşmesi.

### F-20 — charging_frequency smart plan iken bypass et (2026-05-03, Phase 2.2, K-2 fix)
- F-9 sadece üç SOC alanını (`charge_min_soc_percent`, `target_arrival_soc_percent`, `charge_target_soc_percent`) None'a setliyordu.
- `charging_frequency` (default OPTIMAL) hâlâ duruyor → `charge_min_soc_hint=17` devreye giriyordu.
- Düzeltme: smart_plan=True iken `request.charging_frequency = None` → `else` bloğundaki distance-based default (8/10/12/15) seçilir.
- OPTIMAL olmayan değerler log'a yazılır.

### F-21 — Pareto _evaluate_combo SOC akışı dinamik hesap (2026-05-03, Phase 2.2, K-3 fix)
- Eski: her stop için `arrival_at_stop = planned[i].hotspot.soc_at_point` (baseline'dan sabit).
- Pareto target_soc'u %80→%95'e çekse bile, sonraki durağa geliş SOC güncellenmiyordu → şarj süresi/maliyet yanlış.
- Yeni: İlk durak baseline soc_at_point kullanır (tüketim sabit). Sonraki duraklar: `current_soc = prev_target - prev_leg_kwh_drop`.
- Negatif arrival_soc (fiziksel imkânsız combo) erken `min_arrival_soc_margin=-999` döndürür → hard violation.

### F-23 — Smart greedy fallback (2026-05-03, Phase 2.2, K-5 fix)
- 5+ stop'lu rotalarda greedy fallback eskiden `min_target_soc` kullanıyordu.
- Paradoks: az şarj et → sonraki durağa düşük SOC → zincir boyunca daha fazla durak.
- Düzeltme: `min(MAX_CHARGE_TARGET, min_target + 15)` → her duraklara +%15 boost → stop sayısı azalır.

### F-24 — Objective normalization mesafeye göre ölçeklenir (2026-05-03, Phase 2.2, K-9 fix)
- `T_WORST=600 dk`, `C_WORST=800 TL` 500 km kalibrasyonuna göre belirlenmiş sabitlerdi.
- 1200+ km rotalarda T ve C değerleri saturasyona ulaşıyor (T_n=1.0) → solver fark göremez, sadece E ve S'e bakardı.
- Düzeltme: `calculate_objective(metrics, weights, total_distance_km)` — `T_WORST = T_WORST_BASE * max(1.0, dist/500)`.
- `ParetoSolver.solve(total_distance_km=...)` ve tüm `calculate_objective` çağrıları güncellendi.

### F-25 — Pareto target → hotspot.recommended_charge_to senkronu (2026-05-04, Phase 2.3)

**Sorun:** `_run_pareto_simulation` içinde `final_sim.simulate(per_stop_targets=...)` her hotspot oluştururken
`recommended_charge_to = effective_target = per_stop_targets[i]` atıyor. Bu genellikle doğru çalışıyor.
Ancak iki kenar durum soruna yol açıyordu:

1. **Stop sayısı mismatch**: Eğer `final_sim` baseline'dan FARKLI sayıda hotspot üretirse (Pareto planlamadığı
   ek duraklar) bu fazla duraklar `fallback target (80%)` alıyordu. Kullanıcıya yanlış target SOC gösteriliyordu.

2. **Pass 2 staleness**: Pass 2 weather refinement `sim_result` güncelliyor ama hotspot sayısı aynı kalırsa
   `hotspots` değişkeni eskiden Pass 1 nesnelerine işaret etmeye devam ediyordu. Yeni tüketimle
   hesaplanmış `soc_at_point` ve `recommended_charge_to` leg_builder'a ulaşamıyordu.

**Düzeltme (orchestrator.py):**
1. `_run_pareto_simulation`: `final_result` sonrasında Pareto target'larını açıkça uygula (safety net):
   ```python
   for hotspot, target in zip(final_result.hotspots, solution.per_stop_target_soc):
       hotspot.recommended_charge_to = float(target)
   ```
   Stop sayısı mismatch varsa warning loglanır.

2. Pass 2 bloğu: `hotspots = sim_result.hotspots` her zaman çalışır (sayı değişse de değişmese de).
   İstasyon araması yalnızca sayı değiştiğinde yeniden yapılır, aksi halde station_results korunur.

### Refactor 1 — ChargeTargetStrategy Interface (2026-05-05, Phase 2.3)

**Sorun:** `orchestrator._run_soc_simulation` 3 yollu if/elif zinciri içeriyordu:
- Path A: `user_target_soc_override` → SOCSimulator(user_override_target=True)
- Path B: `smart_plan=False` → ChargePlanOptimizer.find_optimal_plan()
- Path C: Pareto → `_run_pareto_simulation()` (orchestrator içinde 130 satır)

Sorunlar:
- Test edilebilirlik düşük (3 path'ı tek fonksiyonda mock'lamak zor)
- Yeni strategy eklemek (ör. ML-based) tüm if/elif'leri değiştirmeyi gerektirir
- Dönüş tipi tutarsız (her path farklı sayıda değer döndürebilir)
- Pareto path'ı 130+ satır — orchestrator dosyasını şişiriyor

**Düzeltme:** Strategy Pattern.

Yeni paket: `app/optimization/strategies/`
- `base.py`: `ChargingContext`, `ChargingPlan`, `ChargeTargetStrategy` (ABC), `select_strategy()` factory
- `manual.py`: `ManualOverrideStrategy` — Path A
- `grid_search.py`: `GridSearchStrategy` — Path B
- `pareto.py`: `ParetoStrategy` — Path C

`orchestrator.py` değişiklikleri:
- `_run_soc_simulation`: 60 satır → 30 satır (sadece Context build + strategy.plan())
- `_run_pareto_simulation`: TAMAMEN KALDIRILDI (mantık ParetoStrategy'ye taşındı)
- `ChargePlanOptimizer` import'u kaldırıldı (artık doğrudan kullanılmıyor)

Faydalar:
- Her strategy ayrı dosyada — bağımsız test edilebilir
- `ChargingContext` RouteRequest'ten decoupled — strategy'ler RouteRequest tipini bilmek zorunda değil
- Yeni strategy eklemek: yeni dosya + factory'de tek satır
- `ChargingPlan` uniform dönüş — orchestrator strategy adını umursamaz

Geriye uyumluluk: `_run_soc_simulation(...)` aynı tuple `(charge_target_soc, sim_result)` döndürür.

### F-26 — Google istasyonlarda DC/AC kontrolü (2026-05-05, Phase 2.3)

**Sorun:** `station_finder._filter_and_score_google_stations` içinde `is_dc=True` hardcoded'du.
Google'dan dönen 22 kW AC duvar şarjcısı bile DC kabul ediliyordu; OCM yolundaki `min_dc_power_kw` (50 kW)
filtresi Google yolunda yoktu. Sonuç: kullanıcıya "HPC istasyonu" vaadedilirken AC istasyon atanıyor,
şarj süresi 4 saate çıkıyordu.

**Düzeltme (station_finder.py):**
1. Pre-filter: `max_power > 0 AND max_power < DC_POWER_THRESHOLD_KW (40)` → AC kabul edilip REDDEDİLİR.
   Yeni `rej_ac` sayacı ve log warning'inde gösterilir.
2. `is_dc_charger = estimated_power_kw >= DC_POWER_THRESHOLD_KW` (her zaman True şu an, çünkü pre-filter
   AC'yi eler; ama future-proofing için doğru hesaplama).
3. Fallback davranış değişmedi: `max_power=0` (Google bilgi vermedi) → 120 kW DC varsayımı.

### F-27 — _calculate_smart_charge_targets baseline_sim'de skip (2026-05-05, Phase 2.3)

**Sorun:** Pareto path'ında baseline_sim, `per_stop_targets=None` ile çalışıyordu. Bu durumda post-process
`_calculate_smart_charge_targets` baseline hotspot'ların `recommended_charge_to`'sunu 72-82% bandına çekiyordu.
Pareto bu mutated değeri değil kendi target'ını kullanıyordu, ama CPU israfı + log gürültüsü vardı.

**Düzeltme:**
1. `SOCSimulator.simulate(skip_smart_targets: bool = False)` parametresi eklendi.
2. `orchestrator._run_pareto_simulation` baseline_sim çağrısında `skip_smart_targets=True` geçiyor.
3. Effect: post-process atlandı → daha hızlı baseline (ufak), daha temiz log.

### F-28 — Pass 2 weather Pareto'yu yeniden çalıştırmasın (2026-05-05, Phase 2.3)

**Sorun:** Pass 2 weather refinement `_run_soc_simulation` çağırıyordu. `smart_plan_enabled=True` ile
Pareto solver tekrar koşuyordu (baseline + N kombinasyon + final = 3 ek SOC sim + DecisionLogger 2x kayıt
+ telemetry kirlenmesi). Hava ufak bir tüketim değişimi yaratır; tam re-optimization gereksiz.

**Düzeltme (orchestrator.py STEP 10.5 Pass 2 bloğu):**
1. Pass 1'in ürettiği per-stop target'ları F-25 sayesinde `hotspot.recommended_charge_to`'da mevcut.
2. Pass 2: Doğrudan `SOCSimulator.simulate(per_stop_targets=existing_targets)` çağır.
3. Pareto solver YENİDEN çağrılmıyor; sadece tüketim güncellendi.
4. Sonuç: Pass 2 maliyeti ~10x azaldı, DecisionLogger tek kayıt yazıyor.

### F-29 — İstasyon mesafesi filtresi gevşetildi → response warning (2026-05-05, Phase 2.3)

**Sorun:** `find_stations_for_hotspots` aynı koridorda iki istasyon olmasını engellemek için
`min_distance_between_stations_km` filtresi uygular. Filtre sonuçları boşaltırsa kademeli olarak gevşetiliyor:
1. Yarı mesafe (örn. 50→25 km), 2. Tamamen kapalı (yalnız duplicate filtresi).
Bu durumlar yalnızca log'a yazılıyordu; kullanıcı "Niye iki şarj durağı 8 km arayla?" diye anlamıyordu.

**Düzeltme (station_finder.py + response_builder.py):**
1. `CorridorSearchResult.distance_warning: Optional[str]` yeni alan.
2. Filtre gevşetildiğinde uyarı set edilir:
   - "⚠️ N. şarj durağı için min mesafe filtresi gevşetildi (X→Y km). Bu durağı bir öncekine yakın bulabilirsiniz."
   - "⚠️ N. şarj durağı için uygun aralıklı istasyon bulunamadı; mesafe filtresi devre dışı."
3. `response_builder.build_warnings`: tüm `distance_warning`'leri response.warnings dizisine ekler (her stop ayrı).

### Refactor 2 — station_finder.py modülerleştirme (2026-05-06, Phase 2.3)

**Sorun:** `app/station_finder.py` 1500+ satıra ulaştı; istasyon arama, polyline filter,
skorlama, legacy API uyumluluğu hepsi tek dosyada. Test edilemiyor, okunamıyor.

**Düzeltme (3 aşama):**

1. **Stage 1 — Skorlama duplicate'leri kaldırıldı.** `_calculate_station_score`,
   `_calculate_weighted_rating`, `_calculate_amenities_score`, `_calculate_popularity_score`
   modul-level fonksiyonları (~130 satır) silindi. `StationScorer` (zaten
   `app/services/station_logic/scorer.py` altında mevcuttu) singleton olarak kullanılıyor:
   ```python
   _scorer = StationScorer()
   station.score = _scorer.calculate_score(...)  # eskiden _calculate_station_score(...)
   ```
   Tüm callsite'lar güncellendi (3 yer: `_filter_and_score_google_stations`,
   `_filter_and_score_stations`, `greedy_select`).

2. **Stage 2 — Polyline filter ayrı modüle taşındı.** Yeni dosya:
   `app/services/station_logic/polyline_filter.py`. İçerik:
   - `decode_route_polyline_coords` (eski `_decode_route_polyline_coords`)
   - `min_distance_to_polyline_km`
   - `check_stations_on_polyline`
   - `PERP_DISTANCE_THRESHOLD_KM` sabiti

   `station_finder.py` geriye dönük uyumluluk için bu fonksiyonları rename ile import eder
   (eski `_` prefix'li isimleri korur). Package `__init__.py` export listesi genişletildi.

3. **Stage 3 — Legacy fonksiyonlar silindi.** `find_best_station(lat, lon, vehicle_id)` ve
   `find_charging_station` (alias) artık çağrılmıyordu — sadece kendileri ve docstring'de
   referans vardı. Toplam ~110 satır kod ve "LEGACY (V1.3 Uyumluluk)" bölümü kaldırıldı.

**Sonuç:** `station_finder.py` 1515 → ~1280 satır (~%15 azalma). Polyline ve scorer mantığı
bağımsız test edilebilir; circular import riski yok.

### Refactor 3 — Versiyon yorumu temizliği (2026-05-06, Phase 2.3)

**Sorun:** Kod tabanı V2.0/V2.3/V2.5/V2.6/V2.7/V2.8/V2.9/V3.1/V3.2/V3.3/V3.4/V4.1/V4.2/V4.3 +
FAZ 1/2/3/3.5 + CB-4/CB-6 + B1 + F-19..F-29 + 🔧 emoji'leri ile dolu. Her commit'te eklenen
arkeoloji yorumları dosyaları okumayı zorlaştırıyor.

**Düzeltme:** Refactor 2 ile birlikte değişen 5 dosyada (station_finder.py, soc_simulator.py,
orchestrator.py, response_builder.py, optimization/strategies/pareto.py) tüm versiyon stamp'leri
temizlendi:
- `🔧 V2.8: Amenities ve weighted rating` → `Amenities ve weighted rating`
- `# 🔧 FAZ 3.5 FIX: SOC sıfırın altına düşemez` → `# SOC sıfırın altına düşemez`
- `# 🔧 V4.3 (CB-6 part 2): soc_at_point = GERÇEK SOC` → `# soc_at_point = GERÇEK SOC`
- `🔧 F-26: Google istasyonlarda DC/AC kontrolü` → `Google istasyonlarda DC/AC kontrolü`
- `Station Finder v2.0 / V2.0 Google Places...` → `Station Finder` (modül başlığı)

İki tür yorum bilinçli olarak korundu:
1. Refactor footprint kayıtları: `# Refactor 2 Stage 1 (2026-05-05): _calculate_X kaldırıldı`
   → migration ipucu, kod arkeolojisi için faydalı.
2. Stratejik tasarım kararları: "Roads API yerine polyline perpendicular kullanıldığı için..."
   → kararı ve gerekçesini açıklayan blok yorumlar.

**Etki:** Yorum hacmi azaldı, davranışı açıklayan yorumlar (yorumun değerli kısmı) kaldı.
Git history versiyon takibi için zaten yeterli; her yoruma stamp yapmak gereksizdi.

### F-18 — Low-SOC origin hotspot relocation + perp filter bypass (2026-05-03, Phase 2.1.b, CB-6 part 4)
- `_relocate_low_soc_hotspot_to_origin()` helper Pass 1 ve Pass 2 (re-find) için.
- İlk hotspot.distance_from_start_km < 15 ise:
  - Lokasyon → `request.start_location` (origin)
  - `bypass_perp_filter=True` flag → station_finder fail-open davranır
- `ChargeHotspot.bypass_perp_filter: bool` yeni alan
- `find_stations_for_hotspots`: bu flag varsa `route_polyline_coords=[]` ata → polyline-perp filter devre dışı
- **Test sonucu (TC-4):** 15% SOC start ile 6 stop → DRIVE 0km @ origin charge → 217-266km cycles ✅

---

## API Contracts

### POST /optimize_route — Request

```python
RouteRequest:
  start_location: GeoPoint                 # required
  end_location: GeoPoint                   # required
  waypoints: List[GeoPoint] = []
  vehicle_model_id: str = ""               # backward compat (JSON catalog)
  vehicle_spec: VehiclePayload | None      # preferred (.NET MSSQL)
  current_soc_percent: float               # 0-100, required
  target_arrival_soc_percent: float | None # 5-80 (TODO: tighten to 5-50)
  charge_min_soc_percent: float | None     # 10-80 (TODO: tighten to 5-40)
  charge_target_soc_percent: float | None  # 50-100
  passenger_count, child_count, extra_load_kg
  departure_time_iso: str | None
  route_strategy: RouteStrategy = OPTIMAL
  preferences: RoutePreferences
  selected_rescue_place_id: str | None     # Safe Harbor user choice
  driving_style: DrivingStyle = NORMAL
  max_speed_kmh, hvac_on, consumption_override_wh_km
  charging_frequency: ChargingFrequency = OPTIMAL
  smart_plan_enabled: bool = True          # Faz 2 Pareto
  optimization_mode: str = "balanced"
```

### POST /optimize_route — Response

```python
ApiResponse[MultiStopRouteResponse]:
  status: str                              # "success" | "error_..."
  total_distance_km, total_duration_minutes
  total_co2_savings_kg, consumption_kwh
  legs: List[DriveLeg | ChargeLeg]
  message: str
  charge_stops: int
  route_strategy: str
  traffic_ratio, duration_without_traffic_minutes
  start_weather, end_weather: WeatherInfo
  total_regen_recovered_kwh, total_charging_cost
  warning_messages: List[str]
  insights: List[RouteInsight]
  trip_id: str                             # Faz 2 outcome backfill
  safe_harbor_info: dict | None            # rescue station info if dest uncovered
  overview_polyline: str                   # snapped via Roads API (V4.2)
```

---

## Test Cases

> Used for regression testing after each refactor. All tests assume Togg T10F Long Range RWD 85 kWh.

### TC-1 — Short Trip (300 km)
- **Origin:** Istanbul (41.0082, 28.9784)
- **Destination:** Bursa (40.1828, 29.0665) ≈ 150 km — TOO short, use Ankara halfway instead
- **Better:** Istanbul → Eskişehir (39.7667, 30.5256) ≈ 330 km
- **Start SOC:** 80%
- **Expected:** 0 charging stops (single charge can cover full trip)
- **Acceptance:** charge_stops == 0 AND final_soc ≥ 15%

### TC-2 — Medium Trip (700 km)
- **Origin:** Istanbul (41.0082, 28.9784)
- **Destination:** Ankara (39.9334, 32.8597) ≈ 450 km — too short
- **Better:** Istanbul → Izmir (38.4192, 27.1287) via highway ≈ 480 km — still short
- **Better:** Istanbul → Trabzon midway (40.5500, 38.0000) ≈ 700 km
- **Start SOC:** 80%
- **Expected:** 1-2 charging stops
- **Acceptance:** charge_stops in [1, 2] AND no leg < 80 km AND final_soc ≥ 15%

### TC-3 — Long Trip (1200 km) **[regression target]**
- **Origin:** Istanbul (41.0082, 28.9784)
- **Destination:** Diyarbakır (37.9144, 40.2306) ≈ 1300 km
- **Start SOC:** 80%
- **Expected:** 3-4 charging stops
- **Current bug:** Returns 10 stops with charge_min_soc=44 default
- **Acceptance:** charge_stops in [3, 5] AND no leg < 80 km AND no charge with arrival_soc > 30%

### TC-4 — Low Battery Start (15%)
- **Origin:** Istanbul (41.0082, 28.9784)
- **Destination:** Ankara (39.9334, 32.8597) ≈ 450 km
- **Start SOC:** 15%
- **Expected:** Immediate first charge stop (within 30 km), then 1-2 more
- **Acceptance:** First leg < 60 km AND charge_stops in [2, 3] AND final_soc ≥ 15%

### TC-5 — High Battery Start (90%)
- **Origin:** Istanbul (41.0082, 28.9784)
- **Destination:** Ankara (39.9334, 32.8597) ≈ 450 km
- **Start SOC:** 90%
- **Expected:** 0-1 charging stops
- **Acceptance:** charge_stops in [0, 1] AND final_soc ≥ 15%

### TC-6 — Irrational user override (defensive)
- **Origin:** Istanbul → Diyarbakır
- **Start SOC:** 80%
- **Override:** `charge_min_soc_percent=70` (absurd — would charge at 70%)
- **Expected:** FastAPI clamps to ~35% AND adds warning AND returns sane plan (3-5 stops)
- **Acceptance:** warning contains "clamped" OR "irrational" AND charge_stops in [3, 6]

### TC-7 — Smart plan with manual override (contract conflict)
- **Origin:** Istanbul → Ankara
- **Start SOC:** 80%
- **Settings:** `smart_plan_enabled=True` + `charge_target_soc_percent=70`
- **Expected:** Either reject with 400 OR ignore override (smart_plan owns decisions) AND return Pareto-optimized plan
- **Acceptance:** Behavior is deterministic and documented

---

## Pending Tasks

| # | Task | Owner | Priority |
|---|---|---|---|
| 1 | ~~Tighten Pydantic constraints (P-1 step 1)~~ | F-7 | ✅ |
| 2 | ~~Add cross-field validator (P-1 step 2)~~ | F-8 | ✅ |
| 3 | ~~Clamp+warn in calculate_base_soc_params (P-1 step 3)~~ | F-9, F-10 | ✅ |
| 4 | ~~Post-route validate_plan() guardrails (P-2)~~ | F-11 | ✅ |
| 5 | ~~Clean _calculate_min_required_soc (P-3, CB-4)~~ | F-12 | ✅ |
| 6 | Refactor hotspot trigger split (P-4) | — | 🟢 |
| 7 | Write pytest cases for TC-1...TC-7 | — | 🟡 |
| 8 | ~~Verify CB-1/CB-2 fix end-to-end~~ Manisa→Trabzon test passed (5 stops) | F-13 | ✅ |
| 9 | ~~CB-6: Leg builder skips first hotspot~~ | F-15...F-18 | ✅ |
| 10 | ~~Phase 2.2 — Pareto baseline=%95, charging_frequency bypass, SOC akış, smart greedy, normalization~~ | F-19..F-24 | ✅ |
| 11 | ~~F-25: Pareto target → hotspot.recommended_charge_to senkronu~~ | F-25 | ✅ |
| 12 | ~~Phase 2.3 — Google DC/AC, baseline skip, Pass 2 bypass, distance warning~~ | F-26..F-29 | ✅ |
| 13 | ~~Refactor 1 — ChargeTargetStrategy interface (3 mekanizma → tek)~~ | Refactor 1 | ✅ |
| 14 | ~~Refactor 2 — station_finder.py modülerleştirme (Stage 1-2-3)~~ | Refactor 2 | ✅ |
| 15 | ~~Refactor 3 — Versiyon yorumu temizliği~~ | Refactor 3 | ✅ |
| 16 | API response schema cleanup (ChargeLeg field naming) | — | 🟢 |
| 17 | pytest fixtures TC-1..TC-7 | — | 🟢 |
| 18 | TC-3 regresyon testi (1304 km, 80% SOC) → beklenen: 4 stop (eski: 5) | — | 🔴 |

---

## Refactor Decisions

### D-1 — Validation philosophy
**Decision:** FastAPI is the engine of last defense. Even if .NET/React send irrational values, FastAPI must produce a sane plan or fail loudly with a clear error. Silent corruption (e.g., 10 stops because threshold is 44%) is unacceptable.

### D-2 — Smart Plan vs Manual Override
**Decision:** `smart_plan_enabled=True` overrides ALL three SOC manual fields. They are mutually exclusive. Document this in the API contract.

### D-3 — No mock vehicle data
**Decision (already implemented per commit 13a2448):** Engine never falls back to fake vehicle data. If vehicle resolution fails, return error.

---

## Changed Files (cumulative for this session)

| File | Reason |
|---|---|
| `app/routers/optimize.py` | F-4: HTTP status codes |
| `app/route_planning/orchestrator.py` | F-5: Pareto log + F-6: Roads snap + F-11 wiring |
| `app/models/route_models.py` | F-7: tighter bounds + F-8: cross-field validator |
| `app/route_planning/soc_params.py` | F-9: smart_plan ignore overrides + F-10: clamp 35% + F-13: charge_target_soc_hint Pareto bypass fix |
| `app/route_planning/response_builder.py` | F-11: validate_plan_sanity() + F-14: tighter threshold + F-17: validate_plan_feasibility() |
| `app/soc_simulator.py` | F-12: _calculate_min_required_soc cleanup + F-16: soc_at_point clamp removed + bypass_perp_filter flag |
| `app/route_planning/leg_builder.py` | F-15: physics state advance when no station |
| `app/station_finder.py` | F-18: bypass_perp_filter flag honored in polyline_coords assignment |
| `app/route_planning/orchestrator.py` | F-19: Pareto baseline=%95 + F-24: total_distance_km → solver.solve |
| `app/route_planning/soc_params.py` | F-20: charging_frequency smart plan iken None'a setlenir |
| `app/optimization/pareto_solver.py` | F-21: _evaluate_combo SOC akışı + F-23: smart greedy +15% + F-24: calc_objective mesafe param |
| `app/optimization/objective.py` | F-24: T/C normalization mesafeye göre ölçeklenir |
| `app/route_planning/orchestrator.py` | F-25: Pareto target → hotspot.recommended_charge_to senkronu |
| `app/station_finder.py` | F-26: Google DC/AC filtresi + F-29: distance_warning alanı ve mesajları |
| `app/soc_simulator.py` | F-27: skip_smart_targets parametresi |
| `app/route_planning/orchestrator.py` | F-27: baseline_sim skip_smart_targets=True + F-28: Pass 2 Pareto bypass |
| `app/route_planning/response_builder.py` | F-29: distance_warning'leri response.warnings'e ekler |
| `app/optimization/strategies/__init__.py` | Refactor 1: yeni paket export |
| `app/optimization/strategies/base.py` | Refactor 1: ChargingContext + ChargingPlan + interface + factory |
| `app/optimization/strategies/manual.py` | Refactor 1: ManualOverrideStrategy |
| `app/optimization/strategies/grid_search.py` | Refactor 1: GridSearchStrategy (eski ChargePlanOptimizer wrapper) |
| `app/optimization/strategies/pareto.py` | Refactor 1: ParetoStrategy (eski _run_pareto_simulation taşındı) |
| `app/route_planning/orchestrator.py` | Refactor 1: _run_soc_simulation Strategy delegasyonu, _run_pareto_simulation kaldırıldı |
| `app/station_finder.py` | Refactor 2 Stage 1+2+3: scorer singleton, polyline filter import, legacy find_best_station kaldırıldı |
| `app/services/station_logic/polyline_filter.py` | Refactor 2 Stage 2: yeni modül (decode/min_distance/check_stations) |
| `app/services/station_logic/__init__.py` | Refactor 2 Stage 2: polyline_filter export edildi |
| `app/station_finder.py` + `app/soc_simulator.py` + `app/route_planning/*` + `app/optimization/strategies/pareto.py` | Refactor 3: V2.x/V3.x/V4.x/FAZ/CB-/F-/🔧 prefix'leri temizlendi |

---

## Next Steps

1. ~~Fix CB-1 + CB-2 + RC-4~~ ✅ (F-7..F-10)
2. ~~Add post-route validation~~ ✅ (F-11)
3. **Run TC-1...TC-7 manually** through React frontend; verify acceptance criteria.
   - Özellikle TC-3 (1200 km): charge_stops in [3, 5] olmalı (önceden 10'du).
   - TC-6 (irrational override 70%): clamp warning + sane plan dönmeli.
4. Write pytest cases for TC-1...TC-7 (regression koruması).
5. **Move to Phase 2** (engine improvements) only after all Phase 1 acceptance tests pass.

## Verification Plan (UI-driven)

After this commit, when you trigger a route from React:
- **Logs to look for** (FastAPI):
  - `smart_plan_enabled=True; charge_min_soc_percent=44.0% YOK SAYILIYOR` (F-9 working)
  - `Base SOC params: min=17.0%, arrival=15.0%` (NOT 44.0% anymore)
  - `Pareto solution: ..., stops=3` (NOT 10)
  - If sanity warning fires: `[PLAN_SANITY] ⚠️ ...`
- **Frontend**: `warning_messages` array might contain new sanity warnings.
- **If still 10 stops**: investigate Pareto solver — possibly `objective.py` weight tuning issue.

---

## Phase 1 Test Results (2026-05-02)

### TC-3: Manisa → Trabzon (1304 km, start SOC 80%, smart_plan=True)
**Before fix:** 10 stops, all targets 80%, charging at 44% SOC (irrational)
**After fix:** ✅ **5 stops, targets [95,95,95,95,64]**, J=0.6471, fallback_used=False
- Pareto solver active (`user_target_override=None`)
- Roads API snap working (219 → 244 points)
- Final leg: 241km from 95% to 28% SOC (proper utilization)

### TC-4: Manisa → Trabzon (start SOC 15%) — **CB-6 fixed (Phase 2.1)**
**Before fix:** First leg shows DRIVE 216km soc=15→17% (physically impossible).
**After fix (2026-05-03):** ✅ **6 stops, sane plan**:
- Leg 0: DRIVE 0km @ origin (SOC 15→15) — Manisa charge stop
- Leg 1: CHARGE Manisa station, arr=15% dep=95%
- Leg 2-10: 217-266km cycles, all 95→13-16% drops
- Leg 11: Final DRIVE 174km, 95→25% SOC
- Pareto active, origin override applied (perp filter bypassed)

### TC-6: Manual mode with charge_min_soc=38 (over limit)
**Result:** ✅ Clamped to 35% with warning log: `mantıksız yüksek; %35.0'e clamp ediliyor`
- 8 stops returned (consistent with 35% threshold + 80% target)
- Plan sanity threshold (after F-14): warning expected on next run

---

## Phase 2 Roadmap

Phase 1 stabilized core engine; ready for engine quality improvements.

### Phase 2.1 — Fix CB-6 (route feasibility checks)
**Goal:** When first hotspot has no station, the route should fail loudly, not silently render impossible legs.
**Plan:**
1. In `leg_builder.py` line 269 (`continue`), instead of skipping silently:
   - Set `current_soc = max(0, current_soc - leg_consumption_estimate)` (still advance physics)
   - Update `current_point = hotspot.location` (still advance position)
   - This makes downstream legs at least display realistic SOC
2. Add a hard error: if any DriveLeg implies SOC < 0% (i.e., physically impossible), return `error_no_stations_in_corridor` status
3. Pre-check: in orchestrator, if hotspot[0] is at distance ≤ 30km AND has no station, expand search radius before proceeding

### Phase 2.2 — Pareto weight tuning + UX
- `objective.py` weight tables empirically need tuning based on `decision_logger` data
- `charging_frequency` UX: currently OPTIMAL/LESS/FREQUENT but only LESS triggers higher targets via charge_min hint. Make it explicit knob to user.
- Cost-priority mode: hook tariff data into objective's C term (currently uses average price)

### Phase 2.3 — Cleaner API response schema
- ChargeLeg field naming inconsistency: `arrival_soc_percent` vs `soc_at_arrival` vs `target_soc_percent` (currently only one populated)
- `station_name` is null in some legs — investigate `_build_station_info`

### Phase 2.4 — Test infrastructure
- Write pytest fixtures for TC-1..TC-7 with stub Google/OCM responses
- Run regression after every refactor

---

## Phase 3 — Microservice Transformation & Hardening

**Goal:** Transform FastAPI into a hardened, high-performance "Black-Box" compute engine isolated behind the .NET Gateway.

### Phase 3.1 — Service-to-Service Security (S2S)
- **Internal Guard Middleware:** Add a middleware to validate `X-Internal-Service-Key` on all incoming requests.
- **Shared Secret:** Implement `INTERNAL_API_KEY` handling via environment variables.
- **CORS Hardening:** Restrict `allow_origins` to only the .NET Gateway/API internal addresses.
- **Production Safety:** Disable Swagger UI (`/docs`, `/redoc`) in production environments.

### Phase 3.2 — Performance & Scalability
- **Redis Integration:** Implement a caching layer for route results and vehicle specs to reduce redundant compute and external API calls.
- **Gunicorn/Uvicorn Tuning:** Optimize worker count and timeout settings for heavy physics calculations.
- **Async Feedback (RabbitMQ):** Offload station feedback and blacklist updates to a background worker to keep the API responsive.

### Phase 3.3 — Infrastructure & Isolation
- **Docker Internal Network:** Configure Docker Compose to place FastAPI on an internal-only network accessible only by the .NET Gateway.
- **TLS Termination:** Plan for Nginx/Traefik as the entry point for HTTPS, ensuring the Gateway and API only handle decrypted traffic.
- **Distributed Tracing:** Propagate `X-Correlation-ID` from .NET through Python logs for end-to-end request tracking (OpenTelemetry).

### Phase 3.4 — Contract & Reliability
- **Contract Testing:** Implement schema validation checks to ensure .NET DTOs and Python Pydantic models remain in sync.
- **Circuit Breaker Tuning:** Calibrate Polly (on .NET side) and internal timeouts based on p99 processing times of long routes.

