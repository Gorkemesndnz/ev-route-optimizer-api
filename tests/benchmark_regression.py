"""
IYONTREE Benchmark Regression Test
====================================

Faz 0 — Güvenlik Ağı

Bu script, refaktör öncesi API davranışını yakalar ve sonraki fazlarda
regresyon olmadığını garanti eder.

Kullanım:
    # İlk çalıştırma — baseline kaydet:
    python tests/benchmark_regression.py --save-baseline

    # Sonraki çalıştırmalar — baseline ile karşılaştır:
    python tests/benchmark_regression.py

    # Belirli bir tolerans ile çalıştır:
    python tests/benchmark_regression.py --soc-tolerance 3.0 --consumption-tolerance 8.0
"""

import json
import sys
import os
import argparse
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

# Proje kök dizinini sys.path'e ekle
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─────────────────────────────────────────────────────────
# YAPILANDIRMA
# ─────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"
BASELINE_FILE = FIXTURES_DIR / "baseline_istanbul_ankara.json"

# Benchmark rota parametreleri
BENCHMARK_ROUTES = {
    "istanbul_ankara": {
        "description": "İstanbul → Ankara (Standart Otoban Rotası ~450 km)",
        "request": {
            "start_location": {"lat": 41.0082, "lon": 28.9784},
            "end_location": {"lat": 39.9334, "lon": 32.8597},
            "vehicle_model_id": "mg_mg4_electric_51_kwh_2022",
            "current_soc_percent": 85,
        },
    },
}

# Varsayılan toleranslar
DEFAULT_SOC_TOLERANCE = 2.0        # %± SOC farkı
DEFAULT_CONSUMPTION_TOLERANCE = 5.0  # %± toplam tüketim farkı
DEFAULT_DISTANCE_TOLERANCE = 1.0     # km± mesafe farkı


# ─────────────────────────────────────────────────────────
# YARDIMCI SINIFLAR
# ─────────────────────────────────────────────────────────

@dataclass
class ComparisonResult:
    """Tek bir metriğin karşılaştırma sonucu."""
    field: str
    baseline: Any
    current: Any
    passed: bool
    detail: str = ""


class BenchmarkRunner:
    """Canlı API'ye istek atar ve sonuçları karşılaştırır."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url

    def run_route(self, route_config: Dict) -> Dict[str, Any]:
        """Rota isteği yap ve JSON yanıtını döndür."""
        import httpx

        url = f"{self.base_url}/optimize_route"
        request_body = route_config["request"]

        print(f"  📡 POST {url}")
        print(f"     Araç: {request_body['vehicle_model_id']}")
        print(f"     SOC: %{request_body['current_soc_percent']}")

        start = time.time()
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=request_body)
        elapsed = time.time() - start

        print(f"     ⏱️  Yanıt süresi: {elapsed:.1f}s")

        if response.status_code != 200:
            raise RuntimeError(
                f"API {response.status_code} döndü: {response.text[:300]}"
            )

        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(
                f"API başarısız: status={data.get('status')}, "
                f"message={data.get('message', 'N/A')}"
            )

        return data

    def extract_fingerprint(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        API yanıtından karşılaştırılabilir parmak izi çıkar.
        
        Trafik, hava durumu gibi değişken alanlar HARİÇ — sadece
        algoritmik çıktılar dahil edilir.
        """
        legs = data.get("legs", [])
        drive_legs = [l for l in legs if l.get("type") == "drive"]
        charge_legs = [l for l in legs if l.get("type") == "charge"]

        # Her sürüş bacağının SOC bilgileri
        drive_soc_profile = []
        for dl in drive_legs:
            drive_soc_profile.append({
                "leg_index": drive_legs.index(dl),  # API'de leg_index olmayabilir, biz ekleyelim
                "soc_start": dl.get("start_soc_percent"),
                "soc_end": dl.get("end_soc_percent"),
                "distance_km": dl.get("distance_km"),
                "consumption_kwh": dl.get("consumption_kwh"),
            })

        # Her şarj bacağının detayları
        charge_profile = []
        for cl in charge_legs:
            station = cl.get("station", {})
            connectors = station.get("connectors", [])
            power_kw = connectors[0].get("power_kw") if connectors else 0

            charge_profile.append({
                "leg_index": charge_legs.index(cl),
                "station_name": station.get("name"),
                "station_id": station.get("id"),
                "soc_start": cl.get("arrival_soc_percent"),
                "soc_end": cl.get("target_soc_percent"),
                "charge_duration_minutes": cl.get("duration_minutes"),
                "power_kw": power_kw,
            })

        fingerprint = {
            "_meta": {
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "api_status": data.get("status"),
            },
            # Temel metrikler
            "total_distance_km": data.get("total_distance_km"),
            "total_duration_minutes": data.get("total_duration_minutes"),
            "consumption_kwh": data.get("consumption_kwh"),
            "total_co2_savings_kg": data.get("total_co2_savings_kg"),
            "charge_stops": data.get("charge_stops"),
            "total_regen_recovered_kwh": data.get("total_regen_recovered_kwh"),
            "total_charging_cost": data.get("total_charging_cost"),
            # Bacak profilleri
            "drive_soc_profile": drive_soc_profile,
            "charge_profile": charge_profile,
            # Uyarılar
            "warning_count": len(data.get("warning_messages", [])),
            # Safe Harbor
            "safe_harbor_active": bool(data.get("safe_harbor_info")),
        }

        return fingerprint

    def compare(
        self,
        baseline: Dict[str, Any],
        current: Dict[str, Any],
        soc_tolerance: float = DEFAULT_SOC_TOLERANCE,
        consumption_tolerance: float = DEFAULT_CONSUMPTION_TOLERANCE,
        distance_tolerance: float = DEFAULT_DISTANCE_TOLERANCE,
    ) -> Tuple[bool, List[ComparisonResult]]:
        """
        Baseline ile güncel parmak izini karşılaştır.
        
        Returns:
            (all_passed, results_list)
        """
        results: List[ComparisonResult] = []

        # ── 1. Şarj durak sayısı (TAM EŞLEŞMELİ) ──
        b_stops = baseline.get("charge_stops", 0)
        c_stops = current.get("charge_stops", 0)
        results.append(ComparisonResult(
            field="charge_stops",
            baseline=b_stops,
            current=c_stops,
            passed=(b_stops == c_stops),
            detail="Şarj durak sayısı değişti!" if b_stops != c_stops else ""
        ))

        # ── 2. Toplam mesafe (±tolerans km) ──
        b_dist = baseline.get("total_distance_km", 0)
        c_dist = current.get("total_distance_km", 0)
        dist_diff = abs(b_dist - c_dist)
        results.append(ComparisonResult(
            field="total_distance_km",
            baseline=round(b_dist, 1),
            current=round(c_dist, 1),
            passed=(dist_diff <= distance_tolerance),
            detail=f"Fark: {dist_diff:.1f} km (tolerans: ±{distance_tolerance} km)"
        ))

        # ── 3. Toplam tüketim (±%tolerans) ──
        b_cons = baseline.get("consumption_kwh", 0)
        c_cons = current.get("consumption_kwh", 0)
        cons_pct = abs(b_cons - c_cons) / max(b_cons, 0.01) * 100
        results.append(ComparisonResult(
            field="consumption_kwh",
            baseline=round(b_cons, 2),
            current=round(c_cons, 2),
            passed=(cons_pct <= consumption_tolerance),
            detail=f"Fark: %{cons_pct:.1f} (tolerans: ±%{consumption_tolerance})"
        ))

        # ── 4. Sürüş bacakları SOC profili (±%tolerans) ──
        b_drives = baseline.get("drive_soc_profile", [])
        c_drives = current.get("drive_soc_profile", [])

        if len(b_drives) != len(c_drives):
            results.append(ComparisonResult(
                field="drive_leg_count",
                baseline=len(b_drives),
                current=len(c_drives),
                passed=False,
                detail="Sürüş bacağı sayısı değişti!"
            ))
        else:
            for i, (b_drv, c_drv) in enumerate(zip(b_drives, c_drives)):
                for soc_field in ["soc_start", "soc_end"]:
                    b_val = b_drv.get(soc_field, 0) or 0
                    c_val = c_drv.get(soc_field, 0) or 0
                    diff = abs(b_val - c_val)
                    results.append(ComparisonResult(
                        field=f"drive[{i}].{soc_field}",
                        baseline=round(b_val, 1),
                        current=round(c_val, 1),
                        passed=(diff <= soc_tolerance),
                        detail=f"Fark: {diff:.1f}% (tolerans: ±{soc_tolerance}%)"
                    ))

        # ── 5. Şarj istasyonu ID'leri (TAM EŞLEŞMELİ) ──
        b_charges = baseline.get("charge_profile", [])
        c_charges = current.get("charge_profile", [])

        b_station_ids = [c.get("station_id") for c in b_charges]
        c_station_ids = [c.get("station_id") for c in c_charges]

        results.append(ComparisonResult(
            field="station_ids",
            baseline=b_station_ids,
            current=c_station_ids,
            passed=(b_station_ids == c_station_ids),
            detail="" if b_station_ids == c_station_ids else "İstasyon seçimi değişti!"
        ))

        # ── 6. Safe Harbor durumu ──
        b_sh = baseline.get("safe_harbor_active", False)
        c_sh = current.get("safe_harbor_active", False)
        results.append(ComparisonResult(
            field="safe_harbor_active",
            baseline=b_sh,
            current=c_sh,
            passed=(b_sh == c_sh),
        ))

        all_passed = all(r.passed for r in results)
        return all_passed, results


# ─────────────────────────────────────────────────────────
# CLÜ ARAYÜZÜ
# ─────────────────────────────────────────────────────────

def print_results(results: List[ComparisonResult], all_passed: bool):
    """Karşılaştırma sonuçlarını güzel formatta yazdır."""
    print("\n" + "=" * 70)
    print("  BENCHMARK REGRESYON RAPORU")
    print("=" * 70)

    for r in results:
        icon = "✅" if r.passed else "❌"
        print(f"\n  {icon}  {r.field}")
        print(f"       Baseline : {r.baseline}")
        print(f"       Şu an    : {r.current}")
        if r.detail:
            print(f"       → {r.detail}")

    print("\n" + "=" * 70)
    if all_passed:
        print("  ✅ TÜM TESTLER GEÇTİ — Regresyon yok!")
    else:
        failed = sum(1 for r in results if not r.passed)
        print(f"  ❌ {failed} TEST BAŞARISIZ — Regresyon tespit edildi!")
    print("=" * 70 + "\n")


def save_baseline(runner: BenchmarkRunner):
    """Baseline JSON dosyasını kaydet."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    print("\n🔧 BASELINE KAYDETME MODU")
    print("─" * 50)

    for name, config in BENCHMARK_ROUTES.items():
        print(f"\n📍 Rota: {config['description']}")

        data = runner.run_route(config)
        fingerprint = runner.extract_fingerprint(data)

        # Tam yanıtı da sakla (debug için)
        baseline_path = FIXTURES_DIR / f"baseline_{name}.json"
        full_response_path = FIXTURES_DIR / f"full_response_{name}.json"

        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(fingerprint, f, indent=2, ensure_ascii=False)
        print(f"  💾 Fingerprint → {baseline_path}")

        with open(full_response_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  💾 Full response → {full_response_path}")

        # Özet
        print(f"\n  📊 Baseline Özeti:")
        print(f"     Mesafe      : {fingerprint['total_distance_km']:.1f} km")
        print(f"     Tüketim     : {fingerprint['consumption_kwh']:.2f} kWh")
        print(f"     Şarj durak  : {fingerprint['charge_stops']}")
        print(f"     Regen       : {fingerprint['total_regen_recovered_kwh']:.2f} kWh")
        print(f"     Safe Harbor : {'Aktif' if fingerprint['safe_harbor_active'] else 'Pasif'}")

        drive_legs = fingerprint.get("drive_soc_profile", [])
        if drive_legs:
            print(f"\n  🔋 SOC Profili:")
            for dl in drive_legs:
                soc_s = dl.get("soc_start") or "?"
                soc_e = dl.get("soc_end") or "?"
                print(
                    f"     Bacak {dl['leg_index']}: "
                    f"%{soc_s} → %{soc_e}  "
                    f"({dl['distance_km']:.1f} km, {dl['consumption_kwh']:.2f} kWh)"
                )

        charge_legs = fingerprint.get("charge_profile", [])
        if charge_legs:
            print(f"\n  ⚡ Şarj Durakları:")
            for cl in charge_legs:
                soc_s = cl.get("soc_start") if cl.get("soc_start") is not None else "?"
                soc_e = cl.get("soc_end") if cl.get("soc_end") is not None else "?"
                dur = cl.get("charge_duration_minutes") if cl.get("charge_duration_minutes") is not None else 0
                power = cl.get("power_kw") if cl.get("power_kw") is not None else 0
                
                print(
                    f"     Bacak {cl['leg_index']}: "
                    f"{cl['station_name']} "
                    f"(%{soc_s} → %{soc_e}, "
                    f"{dur:.0f} dk, "
                    f"{power} kW)"
                )

    print(f"\n✅ Baseline başarıyla kaydedildi!")
    print(f"   Sonraki adım: Refaktör yapıp `python tests/benchmark_regression.py` çalıştırın.\n")


def run_comparison(runner: BenchmarkRunner, soc_tol: float, cons_tol: float, dist_tol: float):
    """Baseline ile karşılaştır."""
    exit_code = 0

    for name, config in BENCHMARK_ROUTES.items():
        baseline_path = FIXTURES_DIR / f"baseline_{name}.json"

        if not baseline_path.exists():
            print(f"\n❌ Baseline dosyası bulunamadı: {baseline_path}")
            print(f"   Önce `--save-baseline` ile baseline kaydedin.")
            sys.exit(1)

        print(f"\n📍 Rota: {config['description']}")

        # Baseline yükle
        with open(baseline_path, "r", encoding="utf-8") as f:
            baseline = json.load(f)

        captured_at = baseline.get("_meta", {}).get("captured_at", "bilinmiyor")
        print(f"  📦 Baseline zamanı: {captured_at}")

        # Güncel çalıştır
        data = runner.run_route(config)
        current = runner.extract_fingerprint(data)

        # Karşılaştır
        all_passed, results = runner.compare(
            baseline, current,
            soc_tolerance=soc_tol,
            consumption_tolerance=cons_tol,
            distance_tolerance=dist_tol,
        )

        print_results(results, all_passed)

        if not all_passed:
            exit_code = 1

    sys.exit(exit_code)


def main():
    parser = argparse.ArgumentParser(
        description="IYONTREE Benchmark Regresyon Testi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  python tests/benchmark_regression.py --save-baseline   # Baseline kaydet
  python tests/benchmark_regression.py                    # Karşılaştır
  python tests/benchmark_regression.py --soc-tolerance 3  # Daha esnek SOC toleransı
        """
    )
    parser.add_argument(
        "--save-baseline", action="store_true",
        help="Baseline JSON dosyasını kaydet (ilk çalıştırma)"
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="API base URL (varsayılan: http://localhost:8000)"
    )
    parser.add_argument(
        "--soc-tolerance", type=float, default=DEFAULT_SOC_TOLERANCE,
        help=f"SOC fark toleransı %% (varsayılan: {DEFAULT_SOC_TOLERANCE})"
    )
    parser.add_argument(
        "--consumption-tolerance", type=float, default=DEFAULT_CONSUMPTION_TOLERANCE,
        help=f"Tüketim fark toleransı %% (varsayılan: {DEFAULT_CONSUMPTION_TOLERANCE})"
    )
    parser.add_argument(
        "--distance-tolerance", type=float, default=DEFAULT_DISTANCE_TOLERANCE,
        help=f"Mesafe fark toleransı km (varsayılan: {DEFAULT_DISTANCE_TOLERANCE})"
    )

    args = parser.parse_args()
    runner = BenchmarkRunner(base_url=args.base_url)

    if args.save_baseline:
        save_baseline(runner)
    else:
        run_comparison(
            runner,
            soc_tol=args.soc_tolerance,
            cons_tol=args.consumption_tolerance,
            dist_tol=args.distance_tolerance,
        )


if __name__ == "__main__":
    main()
