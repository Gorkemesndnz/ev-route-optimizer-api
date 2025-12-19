#!/usr/bin/env python3
"""
Download Open-EV-Data
======================

Downloads the latest ev-data.json from KilowattApp/open-ev-data GitHub repository.
Saves to data/raw/open_ev_data.json

Usage:
    python scripts/download_open_ev_data.py
"""

import urllib.request
import json
from pathlib import Path
from datetime import datetime, timezone


# Open-EV-Data raw JSON URL
OPEN_EV_DATA_URL = "https://raw.githubusercontent.com/KilowattApp/open-ev-data/master/data/ev-data.json"

# Output path
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_FILE = OUTPUT_DIR / "open_ev_data.json"


def download_open_ev_data() -> bool:
    """
    Download Open-EV-Data JSON file.
    
    Returns:
        True if successful, False otherwise
    """
    print(f"Downloading Open-EV-Data from GitHub...")
    print(f"  URL: {OPEN_EV_DATA_URL}")
    
    try:
        # Create output directory if needed
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        # Download with urllib (no external dependencies)
        with urllib.request.urlopen(OPEN_EV_DATA_URL, timeout=60) as response:
            data = response.read()
        
        # Validate JSON
        parsed = json.loads(data)
        
        # Extract metadata
        meta = parsed.get("meta", {})
        vehicle_count = meta.get("overall_count", 0)
        updated_at = meta.get("updated_at", "unknown")
        brand_count = len(parsed.get("brands", []))
        
        # Save to file
        with open(OUTPUT_FILE, "wb") as f:
            f.write(data)
        
        file_size_kb = len(data) / 1024
        
        print(f"\nDownload successful!")
        print(f"  - File: {OUTPUT_FILE}")
        print(f"  - Size: {file_size_kb:.1f} KB")
        print(f"  - Vehicles: {vehicle_count}")
        print(f"  - Brands: {brand_count}")
        print(f"  - Source updated: {updated_at}")
        print(f"  - Downloaded: {datetime.now(timezone.utc).isoformat()}")
        
        return True
        
    except urllib.error.URLError as e:
        print(f"\nNetwork error: {e}")
        return False
    except json.JSONDecodeError as e:
        print(f"\nInvalid JSON response: {e}")
        return False
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        return False


def main():
    """Main entry point"""
    print("=" * 60)
    print("Open-EV-Data Downloader")
    print("=" * 60)
    
    success = download_open_ev_data()
    
    if success:
        print("\nNext step:")
        print("  python scripts/convert_to_master.py")
    else:
        print("\nDownload failed. Please check your internet connection.")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
