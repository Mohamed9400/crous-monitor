#!/usr/bin/env python3
"""
CROUS Master List Updater (NEW STRATEGY)
1. Fetch all residences from coordinates API
2. Filter for IDF residences
3. For each IDF residence, fetch COMPLETE details including price
4. Only keep residences that are available AND have price
"""

import requests
import json
import re
import os
import time
import asyncio
import aiohttp
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# --- CONFIGURATION ---
COORDINATES_URL = "https://admin-v2.crous-mobile.fr/ws/v1/houses/coordinates"
DETAILS_URL = "https://trouverunlogement.lescrous.fr/api/fr/tools/42/accommodations"
MASTER_LIST_FILE = "idf_master_list.json"
LOG_FILE = "crous_monitor.log"

# Île-de-France Departments
IDF_DEPARTMENTS = {'75', '77', '78', '91', '92', '93', '94', '95'}

# 📍 ÎLE-DE-FRANCE BOUNDING BOX
MIN_LON = 1.5
MAX_LON = 3.56
MIN_LAT = 48.12
MAX_LAT = 49.24

# API Limits
MAX_CONCURRENT_REQUESTS = 10

def get_header() -> Dict:
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    }

def log_message(message: str) -> None:
    """Add message to rotating log file."""
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    log_entry = f"{timestamp} {message}\n"
    
    # Rotate log if too large
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) >= 1000:
            lines = lines[-500:]
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
    
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry)
    
    print(message)

def extract_department_code(address: str) -> Optional[str]:
    """Extract department code from address."""
    if not address:
        return None
    match = re.search(r'\b(\d{5})\b', address)
    return match.group(1)[:2] if match else None

def is_idf_residence(lat: float, lon: float, address: str) -> bool:
    """Check if residence is in Île-de-France."""
    if not (MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lon <= MAX_LON):
        return False
    
    dept_code = extract_department_code(address)
    if dept_code:
        return dept_code in IDF_DEPARTMENTS
    
    return False

async def fetch_residence_details(session: aiohttp.ClientSession, 
                                 residence_id: int, 
                                 semaphore: asyncio.Semaphore) -> Optional[Dict]:
    """Fetch complete residence details including price and availability."""
    async with semaphore:
        url = f"{DETAILS_URL}/{residence_id}"
        params = {"occupationMode": "alone"}
        
        try:
            async with session.get(url, params=params, headers=get_header(), timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                elif response.status == 404:
                    return None  # Residence no longer exists
                else:
                    return None
        except Exception:
            return None

def is_bookable_residence(data: Dict) -> Tuple[bool, Optional[float]]:
    """
    Check if residence is actually bookable.
    Returns (is_bookable, price) where price is in euros.
    """
    # Check availability
    if not data.get("available", False):
        return False, None
    
    # Check occupation modes
    occupation_modes = data.get("occupationModes", [])
    
    for mode in occupation_modes:
        if mode.get("type") == "alone":
            rent = mode.get("rent", {})
            min_price = rent.get("min")
            
            if min_price:
                # Convert cents to euros
                price_eur = min_price / 100
                return True, price_eur
    
    return False, None

async def update_master_list_async() -> None:
    """Main function to update IDF master list with complete details."""
    log_message("🔄 START: Updating CROUS master list (NEW STRATEGY)")
    
    try:
        # 1. Download basic residence list
        log_message("🔗 Fetching coordinates from CROUS API...")
        response = requests.get(COORDINATES_URL, headers=get_header(), timeout=30)
        response.raise_for_status()
        all_residences = response.json()
        
        log_message(f"📥 Downloaded {len(all_residences):,} total residences")
        
        # 2. Filter for Île-de-France (basic filter)
        idf_candidates = []
        for residence in all_residences:
            try:
                residence_id = residence.get("id")
                lat = float(residence.get("lat", 0))
                lon = float(residence.get("lon", 0))
                address = residence.get("address", "")
                
                if is_idf_residence(lat, lon, address):
                    idf_candidates.append({
                        "id": residence_id,
                        "lat": lat,
                        "lon": lon,
                        "title": residence.get("title", "Unknown"),
                        "address": address
                    })
            except Exception:
                continue
        
        log_message(f"📍 IDF candidates found: {len(idf_candidates)}")
        
        # 3. Fetch complete details for all IDF candidates
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        bookable_residences = []
        stats = {
            'total_checked': 0,
            'bookable': 0,
            'not_available': 0,
            'no_price': 0,
            'errors': 0
        }
        
        log_message(f"🔍 Fetching details for {len(idf_candidates)} IDF residences...")
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for candidate in idf_candidates:
                tasks.append(fetch_residence_details(session, candidate["id"], semaphore))
            
            # Process results as they complete
            for i, task in enumerate(asyncio.as_completed(tasks)):
                try:
                    data = await task
                    stats['total_checked'] += 1
                    
                    if stats['total_checked'] % 20 == 0:
                        log_message(f"   Processed {stats['total_checked']}/{len(idf_candidates)}...")
                    
                    if not data:
                        stats['errors'] += 1
                        continue
                    
                    # Check if bookable
                    is_bookable, price = is_bookable_residence(data)
                    
                    if is_bookable and price:
                        # Add to master list
                        candidate = idf_candidates[i]
                        bookable_residences.append({
                            "id": candidate["id"],
                            "title": candidate["title"],
                            "address": candidate["address"],
                            "lat": candidate["lat"],
                            "lon": candidate["lon"],
                            "price": price,
                            "available": True,
                            "source": "master_list",
                            "data": data  # Complete data for reference
                        })
                        stats['bookable'] += 1
                    elif not data.get("available", False):
                        stats['not_available'] += 1
                    else:
                        stats['no_price'] += 1
                        
                except Exception as e:
                    stats['errors'] += 1
                    continue
        
        # 4. Sort by ID for consistency
        bookable_residences.sort(key=lambda x: x['id'])
        
        # 5. Save master list
        with open(MASTER_LIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(bookable_residences, f, indent=2, ensure_ascii=False)
        
        # 6. Log results
        log_message(f"✅ SUCCESS: Master list updated")
        log_message(f"   • Total IDF residences checked: {stats['total_checked']}")
        log_message(f"   • Bookable (with price): {stats['bookable']}")
        log_message(f"   • Not available: {stats['not_available']}")
        log_message(f"   • Available but no price: {stats['no_price']}")
        log_message(f"   • Errors: {stats['errors']}")
        
        log_message(f"💾 Saved {len(bookable_residences)} bookable residences to: {MASTER_LIST_FILE}")
        log_message("🔄 END: Master list update complete")
        
    except Exception as e:
        log_message(f"❌ ERROR: {type(e).__name__} - {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(update_master_list_async())