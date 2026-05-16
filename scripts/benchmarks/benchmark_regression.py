"""
IYONTREE Benchmark Regression Test (v3 Plan)
=============================================

Bu script, Faz 0.5 ve sonrası için delta-toleranslı regresyon testleri çalıştırır.
Sadece şu metrikler kaydedili ve kontrol edilir:
{ hotspot_count, hotspot_positions_km[], final_soc_pct, total_charge_time_min }

Kullanım:
    # Baseline kaydet (B0 veya B1):
    python scripts/benchmarks/benchmark_regression.py --save benchmark/B0.json

    # Kaydedilmiş baseline'a (B1'e) karşı delta toleranslı test et:
    python scripts/benchmarks/benchmark_regression.py --compare benchmark/B1.json
"""

import json
import sys
import argparse
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

BENCHMARK_ROUTES = {
    "istanbul_ankara": {
        "description": "İstanbul → Ankara (Standart Otoban)",
        "request": {
            "start_location": {"lat": 41.0082, "lon": 28.9784},
            "end_location": {"lat": 39.9334, "lon": 32.8597},
            "vehicle_model_id": "mg_mg4_electric_51_kwh_2022",
            "current_soc_percent": 85,
        },
    },
    "ankara_bolu": {
        "description": "Ankara → Bolu (Dağlık - Elevation Testi)",
        "request": {
            "start_location": {"lat": 39.9334, "lon": 32.8597},
            "end_location": {"lat": 40.7392, "lon": 31.6116},
            "vehicle_model_id": "mg_mg4_electric_51_kwh_2022",
            "current_soc_percent": 85,
        },
    },
    "izmir_antalya": {
        "description": "İzmir → Antalya (Uzun Mesafe - Çöl Testi)",
        "request": {
            "start_location": {"lat": 38.4192, "lon": 27.1287},
            "end_location": {"lat": 36.8969, "lon": 30.7133},
            "vehicle_model_id": "mg_mg4_electric_51_kwh_2022",
            "current_soc_percent": 85,
        },
    }
}

class BenchmarkRunner:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url

    def run_route(self, route_config: Dict) -> Dict[str, Any]:
        import httpx
        url = f"{self.base_url}/optimize_route"
        request_body = route_config["request"]

        start = time.time()
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=request_body)
        elapsed = time.time() - start

        if response.status_code != 200:
            raise RuntimeError(f"API {response.status_code} döndü.")

        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError("API başarısız döndü.")

        return data

    def extract_metrics(self, data: Dict[str, Any]) -> Dict[str, Any]:
        legs = data.get("legs", [])
        cumulative_km = 0.0
        hotspot_positions_km = []
        total_charge_time_min = 0.0

        for leg in legs:
            if leg.get("type") == "drive":
                cumulative_km += leg.get("distance_km", 0.0)
            elif leg.get("type") == "charge":
                hotspot_positions_km.append(round(cumulative_km, 1))
                total_charge_time_min += leg.get("duration_minutes", 0.0)

        final_soc_pct = 0.0
        if legs:
            last_leg = legs[-1]
            if last_leg.get("type") == "drive":
                final_soc_pct = last_leg.get("end_soc_percent", 0.0)
            elif last_leg.get("type") == "charge":
                final_soc_pct = last_leg.get("target_soc_percent", 0.0)

        return {
            "hotspot_count": len(hotspot_positions_km),
            "hotspot_positions_km": hotspot_positions_km,
            "final_soc_pct": round(final_soc_pct, 1),
            "total_charge_time_min": round(total_charge_time_min, 1)
        }

    def compare_metrics(self, baseline: Dict[str, Any], current: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors = []
        
        # 1. hotspot_count: ±1
        b_count = baseline["hotspot_count"]
        c_count = current["hotspot_count"]
        if abs(b_count - c_count) > 1:
            errors.append(f"hotspot_count {b_count} -> {c_count} (Tolerans ±1 aşıldı)")

        # 2. final_soc: ±3%
        b_soc = baseline["final_soc_pct"]
        c_soc = current["final_soc_pct"]
        if abs(b_soc - c_soc) > 3.0:
            errors.append(f"final_soc {b_soc}% -> {c_soc}% (Tolerans ±3% aşıldı)")

        # 3. hotspot_positions_km: ±5 km (Eğer sayıları uyuyorsa)
        b_pos = baseline["hotspot_positions_km"]
        c_pos = current["hotspot_positions_km"]
        if len(b_pos) == len(c_pos):
            for i, (b_km, c_km) in enumerate(zip(b_pos, c_pos)):
                if abs(b_km - c_km) > 5.0:
                    errors.append(f"Hotspot {i+1} konumu {b_km}km -> {c_km}km (Tolerans ±5km aşıldı)")
        else:
            errors.append("Hotspot sayısı farklı olduğu için km eşleştirmesi atlandı.")

        return len(errors) == 0, errors

def main():
    parser = argparse.ArgumentParser("IYONTREE Benchmark Runner (v3)")
    parser.add_argument("--save", type=str, help="Baseline dosyasını buraya kaydet (örn: benchmark/B0.json)")
    parser.add_argument("--compare", type=str, help="Çıktıyı kayıttaki baseline ile karşılaştır")
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:8000", help="API URL (varsayılan: 127.0.0.1:8000)")
    args = parser.parse_args()

    runner = BenchmarkRunner(base_url=args.base_url)

    if args.save:
        save_path = Path(args.save)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        results = {}

        print(f"🔧 Baseline {save_path.name} için veri toplanıyor...")
        for name, config in BENCHMARK_ROUTES.items():
            print(f"  -> {config['description']}")
            data = runner.run_route(config)
            metrics = runner.extract_metrics(data)
            results[name] = metrics
            print(f"     {metrics}")

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"✅ Başarıyla kaydedildi: {save_path}")

    elif args.compare:
        compare_path = Path(args.compare)
        if not compare_path.exists():
            print(f"❌ Dosya bulunamadı: {compare_path}")
            sys.exit(1)

        with open(compare_path, "r", encoding="utf-8") as f:
            baseline_data = json.load(f)

        print(f"⚖️ Delta-Toleranslı Karşılaştırma ({compare_path.name})")
        all_passed = True

        for name, config in BENCHMARK_ROUTES.items():
            print(f"\n📍 {config['description']}")
            baseline_metrics = baseline_data.get(name)
            if not baseline_metrics:
                print(f"  Uyarı: Baseline'da '{name}' bulunamadı, atlanıyor.")
                continue

            current_data = runner.run_route(config)
            current_metrics = runner.extract_metrics(current_data)
            
            passed, errors = runner.compare_metrics(baseline_metrics, current_metrics)
            
            if passed:
                print(f"  ✅ GEÇTİ")
            else:
                print(f"  ❌ BAŞARISIZ")
                for err in errors:
                    print(f"     - {err}")
                all_passed = False

        if not all_passed:
            sys.exit(1)
        print("\n🎉 Tüm rotalar delta toleransları dahilinde geçti!")

    else:
        print("Kullanım seçeneği belirtilmedi. --save veya --compare kullanın.")

if __name__ == "__main__":
    main()
