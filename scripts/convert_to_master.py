#!/usr/bin/env python3
"""
Convert Open-EV-Data to Master Format
======================================

Converts raw open_ev_data.json to:
- vehicles_master.json (normalized vehicle specs)
- charge_curves.json (SOC->kW charging curves)

Usage:
    python scripts/convert_to_master.py
"""

import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Set


# Paths
PROJECT_ROOT = Path(__file__).parent.parent
RAW_FILE = PROJECT_ROOT / "data" / "raw" / "open_ev_data.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

# Default weights by vehicle type (when curb_weight is missing)
DEFAULT_WEIGHTS = {
    "car": 1700,
    "suv": 2100,
    "compact": 1500,
    "motorbike": 250,
    "microcar": 600,
}


def sanitize_id(text: str) -> str:
    """Convert text to a valid ID (lowercase, underscores, no special chars)"""
    # Lowercase and replace spaces/hyphens with underscores
    text = text.lower().replace(" ", "_").replace("-", "_")
    # Remove non-alphanumeric except underscores
    text = re.sub(r"[^a-z0-9_]", "", text)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)
    # Strip leading/trailing underscores
    return text.strip("_")


def generate_vehicle_id(brand: str, model: str, variant: str, year: int) -> str:
    """Generate unique vehicle ID from brand/model/variant/year"""
    parts = [sanitize_id(brand), sanitize_id(model)]
    if variant:
        parts.append(sanitize_id(variant))
    parts.append(str(year))
    return "_".join(filter(None, parts))


def convert_connector_type(ports: List[str]) -> str:
    """Convert DC port list to primary connector type"""
    port_priority = ["ccs", "chademo", "tesla_suc"]
    ports_lower = [p.lower() for p in ports]
    
    for port in port_priority:
        if port in ports_lower:
            if port == "tesla_suc":
                return "Tesla_SUC"
            return port.upper()
    
    return "CCS"  # Default


def convert_open_ev_data(raw_path: Path, output_dir: Path) -> Dict[str, int]:
    """
    Main conversion function.
    
    Returns:
        Dict with conversion statistics
    """
    print(f"Loading raw data from: {raw_path}")
    
    with open(raw_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    source_meta = data.get("meta", {})
    source_updated = source_meta.get("updated_at", "unknown")
    
    # Initialize output structures
    vehicles_master = {
        "meta": {
            "version": "2.0",
            "source": "open-ev-data",
            "source_updated_at": source_updated,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "vehicle_count": 0,
        },
        "vehicles": [],
    }
    
    charge_curves = {
        "meta": {
            "version": "1.0",
            "interpolation": "linear",
        },
        "curves": {},
    }
    
    # Track statistics
    stats = {
        "total_processed": 0,
        "vehicles_added": 0,
        "curves_added": 0,
        "real_curves": 0,
        "errors": 0,
        "duplicates_skipped": 0,
    }
    
    seen_ids: Set[str] = set()
    
    for item in data.get("data", []):
        stats["total_processed"] += 1
        
        try:
            brand = item.get("brand", "Unknown")
            model = item.get("model", "Unknown")
            variant = item.get("variant", "")
            year = item.get("release_year", 2023)
            source_id = item.get("id", "")
            
            # Generate vehicle ID
            vehicle_id = generate_vehicle_id(brand, model, variant, year)
            
            # Handle duplicates
            if vehicle_id in seen_ids:
                # Append short source ID to make unique
                if source_id:
                    vehicle_id = f"{vehicle_id}_{source_id[:8]}"
                else:
                    stats["duplicates_skipped"] += 1
                    continue
            
            if vehicle_id in seen_ids:
                stats["duplicates_skipped"] += 1
                continue
                
            seen_ids.add(vehicle_id)
            
            # Battery capacity
            battery_kwh = item.get("usable_battery_size")
            if not battery_kwh or battery_kwh <= 0:
                stats["errors"] += 1
                continue
            
            # Consumption: kWh/100km -> Wh/km
            energy_consumption = item.get("energy_consumption", {})
            avg_consumption = energy_consumption.get("average_consumption", 16.0)
            base_consumption_wh_km = avg_consumption * 10
            
            # DC charger info
            dc_charger = item.get("dc_charger", {})
            dc_max_kw = dc_charger.get("max_power", 50.0)
            dc_ports = dc_charger.get("ports", ["ccs"])
            charging_curve = dc_charger.get("charging_curve", [])
            is_default_curve = dc_charger.get("is_default_charging_curve", True)
            
            # AC charger info
            ac_charger = item.get("ac_charger", {})
            ac_max_kw = ac_charger.get("max_power", 11.0)
            
            # Vehicle type and weight
            vehicle_type = item.get("vehicle_type", "car")
            curb_weight = DEFAULT_WEIGHTS.get(vehicle_type, 1700)
            
            # Charging voltage
            charging_voltage = item.get("charging_voltage", 400)
            
            # Build display name
            display_parts = [brand, model]
            if variant:
                display_parts.append(variant)
            display_name = " ".join(display_parts) + f" ({year})"
            
            # Create vehicle entry
            vehicle_spec = {
                "id": vehicle_id,
                "source_id": source_id,
                "brand": brand,
                "model": model,
                "variant": variant,
                "year": year,
                "display_name": display_name,
                "battery_capacity_kwh": round(battery_kwh, 1),
                "base_consumption_wh_km": round(base_consumption_wh_km, 1),
                "connector_type": convert_connector_type(dc_ports),
                "ac_max_kw": round(ac_max_kw, 1),
                "dc_max_kw": round(dc_max_kw, 1),
                "charging_voltage": charging_voltage,
                "curb_weight_kg": curb_weight,
                "auxiliary_power_kw": 1.2,
                "has_real_curve": not is_default_curve and len(charging_curve) > 0,
                "vehicle_type": vehicle_type,
            }
            
            vehicles_master["vehicles"].append(vehicle_spec)
            stats["vehicles_added"] += 1
            
            # Create charging curve entry if available
            if charging_curve:
                curve_points = []
                for p in charging_curve:
                    curve_points.append({
                        "soc": p.get("percentage", 0),
                        "power_kw": p.get("power", 0),
                    })
                
                charge_curves["curves"][vehicle_id] = {
                    "source": "measured" if not is_default_curve else "estimated",
                    "points": curve_points,
                }
                stats["curves_added"] += 1
                
                if not is_default_curve:
                    stats["real_curves"] += 1
        
        except Exception as e:
            stats["errors"] += 1
            continue
    
    # Update vehicle count
    vehicles_master["meta"]["vehicle_count"] = len(vehicles_master["vehicles"])
    
    # Sort vehicles by display_name
    vehicles_master["vehicles"].sort(key=lambda v: v["display_name"])
    
    # Save output files
    output_dir.mkdir(parents=True, exist_ok=True)
    
    vehicles_path = output_dir / "vehicles_master.json"
    with open(vehicles_path, "w", encoding="utf-8") as f:
        json.dump(vehicles_master, f, indent=2, ensure_ascii=False)
    
    curves_path = output_dir / "charge_curves.json"
    with open(curves_path, "w", encoding="utf-8") as f:
        json.dump(charge_curves, f, indent=2, ensure_ascii=False)
    
    return stats


def main():
    """Main entry point"""
    print("=" * 60)
    print("Open-EV-Data Converter")
    print("=" * 60)
    
    if not RAW_FILE.exists():
        print(f"\nError: Raw data file not found: {RAW_FILE}")
        print("Please run first: python scripts/download_open_ev_data.py")
        return 1
    
    print(f"\nInput: {RAW_FILE}")
    print(f"Output: {OUTPUT_DIR}")
    
    stats = convert_open_ev_data(RAW_FILE, OUTPUT_DIR)
    
    print("\n" + "=" * 60)
    print("Conversion Complete!")
    print("=" * 60)
    print(f"  Total processed: {stats['total_processed']}")
    print(f"  Vehicles added: {stats['vehicles_added']}")
    print(f"  Charging curves: {stats['curves_added']}")
    print(f"  Real curves: {stats['real_curves']}")
    print(f"  Duplicates skipped: {stats['duplicates_skipped']}")
    print(f"  Errors: {stats['errors']}")
    print("\nOutput files:")
    print(f"  - {OUTPUT_DIR / 'vehicles_master.json'}")
    print(f"  - {OUTPUT_DIR / 'charge_curves.json'}")
    
    return 0


if __name__ == "__main__":
    exit(main())
