import asyncio
import aiohttp
import requests
import json
import os
import time
import random
import urllib.parse
import math
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

# --- 1. CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42"
AVAILABILITY_URL = "https://trouverunlogement.lescrous.fr/api/fr/tools/42/accommodations"
HEALTH_URL = "https://trouverunlogement.lescrous.fr/api/health"

# 🏭 TARGET 1: Vallourec Meudon (Work)
WORK_LAT = 48.8207
WORK_LON = 2.2337
WORK_ADDR = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 🎓 TARGET 2: ISTEC Paris (School)
SCHOOL_LAT = 48.8769
SCHOOL_LON = 2.3655
SCHOOL_ADDR = "ISTEC, 128 Quai de Jemmapes, 75010 Paris"

# ⚡ CONCURRENCY LIMIT
MAX_CONCURRENT_REQUESTS = 10

# 📍 STRICT IDF ZONE
PAYLOAD = {
    "idTool": 42,
    "need_aggregation": True,
    "pageSize": 24,
    "sector": None,
    "occupationModes": ["alone"],
    "location": [
        {"lon": 1.4462445, "lat": 49.241431},
        {"lon": 3.5592208, "lat": 48.1201456}
    ],
    "residence": None,
    "precision": 4,
    "equipment": [],
    "adaptedPmr": False,
    "toolMechanism": "flow"
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
]

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
HISTORY_FILE = "history.json"
MASTER_LIST_FILE = "idf_master_list.json"
LOG_FILE = "crous_monitor.log"
HEARTBEAT_INTERVAL = 86400  # 24 hours
FORCE_RELIST = os.getenv("FORCE_RELIST", "false").lower() == "true"

# Colors for Discord embeds
COLORS = {
    "NEW": 5763719,       # Green
    "WAKE_UP": 15105570,  # Orange
    "HIDDEN_GEM": 10181046,  # Purple
    "FORCED": 3447003,    # Blue
    "ERROR": 15548997     # Red
}

# --- 2. LOGGING SYSTEM ---

def log_message(message: str) -> None:
    """Add message to rotating log file."""
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    log_entry = f"{timestamp} {message}\n"
    
    # Rotate log if too large (keep last 1000 lines)
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if len(lines) >= 1000:
            lines = lines[-500:]
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
    
    # Append new log entry
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(log_entry)
    
    print(message)

# --- 3. LOAD MASTER LIST ---

def load_master_list() -> List[Dict]:
    """Load IDF master list - now contains ONLY bookable residences."""
    try:
        if os.path.exists(MASTER_LIST_FILE):
            with open(MASTER_LIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Transform to expected format
                candidates = []
                for item in data:
                    candidates.append({
                        "id": item["id"],
                        "data": item.get("data", {
                            "id": item["id"],
                            "title": item["title"],
                            "address": item["address"],
                            "location": {"lat": item["lat"], "lon": item["lon"]},
                            "occupationModes": [{
                                "type": "alone",
                                "rent": {"min": item["price"] * 100}
                            }]
                        }),
                        "source": "master_list",
                        "price": item["price"]
                    })
                return candidates
        else:
            log_message("⚠️  Master list not found, running in search-only mode")
            return []
    except Exception as e:
        log_message(f"❌ Error loading master list: {e}")
        return []


def extract_residence_id_from_master(item: Dict) -> Dict:
    """Extract basic info from master list item for auditing."""
    return {
        "id": item.get("id"),
        "data": {
            "id": item.get("id"),
            "title": item.get("title", "Unknown"),
            "address": item.get("address", ""),
            "location": {"lat": item.get("lat"), "lon": item.get("lon")}
        },
        "source": "master_list"
    }

# --- 4. MATH & GEOMETRY (UNCHANGED) ---

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

def generate_commute_links(lat, lon):
    now = datetime.now()
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    if target_time < now:
        target_time += timedelta(days=1)
    date_str = target_time.strftime("%Y-%m-%d")
    
    def make_link(dest):
        d_enc = urllib.parse.quote(dest)
        return f"https://www.google.com/maps?saddr={lat},{lon}&daddr={d_enc}&dirflg=r&ttype=dep&date={date_str}&time=07:30"
    
    return make_link(WORK_ADDR), make_link(SCHOOL_ADDR)

# --- 5. ASYNC CORE LOGIC ---

def get_header():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'application/json',
        'Referer': 'https://trouverunlogement.lescrous.fr/'
    }

async def check_availability_async(session, housing_id, semaphore):
    """Check if a residence is actually available for booking."""
    async with semaphore:
        today = datetime.now().strftime("%Y-%m-%d")
        next_year = datetime.now().year + 1
        end_date = f"{next_year}-08-31"
        
        url = f"{AVAILABILITY_URL}/{housing_id}/availabilities"
        params = {
            "occupationMode": "alone",
            "arrivalDate": today,
            "departureDate": end_date
        }
        
        try:
            async with session.get(url, params=params, headers=get_header(), timeout=8) as response:
                if response.status == 200:
                    data = await response.json()
                    return bool(data.get("periodsAvailable"))
                elif response.status == 404:
                    return False  # Residence no longer exists
        except Exception as e:
            return False
        
        return False

async def audit_candidates(candidates: List[Dict]) -> List[Dict]:
    """
    Check availability for search results (master list already validated).
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    results = []
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for candidate in candidates:
            source = candidate.get("source", "unknown")
            
            if source == "master_list":
                # Already validated as bookable, just add to results
                tasks.append((candidate, asyncio.sleep(0)))  # No API call needed
            else:
                # Search result - need to check availability
                housing_id = candidate.get("id")
                tasks.append((candidate, check_availability_async(session, housing_id, semaphore)))
        
        log_message(f"⚡ Auditing {len(tasks)} candidates...")
        audit_results = await asyncio.gather(*[t[1] for t in tasks])
        
        for i, result in enumerate(audit_results):
            candidate = tasks[i][0]
            try:
                # Get stats
                data = candidate.get("data", {})
                stats = get_stats_from_data(data)
                
                # Determine if bookable
                if candidate.get("source") == "master_list":
                    is_bookable = True
                    price = candidate.get("price")
                else:
                    is_bookable = result  # From availability check
                    price = extract_price_from_data(data)
                
                results.append({
                    'id': candidate.get("id"),
                    'data': data,
                    'bookable': is_bookable,
                    'source': candidate.get("source"),
                    'has_price': price is not None,
                    'price': price,
                    'stats': stats
                })
            except Exception as e:
                log_message(f"⚠️  Error processing candidate {candidate.get('id')}: {e}")
                continue
    
    return results

# --- 6. DATA MANAGEMENT ---

def load_data() -> Dict:
    """Load state from history file."""
    default = {
        "all_seen": [],
        "active": [],
        "ghosts": [],
        "hidden_finds": [],
        "last_heartbeat": 0,
        "stats": {
            "total_runs": 0,
            "hidden_gems_found": 0,
            "last_run": None
        }
    }
    
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Migrate old format if needed
                if "hidden_finds" not in data:
                    data["hidden_finds"] = []
                if "stats" not in data:
                    data["stats"] = default["stats"]
                
                return data
        except Exception as e:
            log_message(f"❌ Error loading history: {e}")
    
    return default

def save_data(data: Dict) -> None:
    """Save state to history file."""
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log_message(f"❌ Error saving history: {e}")

# --- 7. DISCORD NOTIFICATIONS ---

def send_discord_alert(title: str, description: str, color: int, url: str = None, 
                       fields: List[Dict] = None, image: str = None) -> bool:
    """Send alert to Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        return False
    
    embed = {
        "title": title,
        "description": description,
        "color": color,
        "footer": {
            "text": f"🤖 CrousBot V8 • {datetime.now().strftime('%H:%M')}"
        }
    }
    
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = fields
    if image:
        embed["thumbnail"] = {"url": image}
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
        time.sleep(0.5)  # Avoid rate limiting
        return response.status_code == 204
    except Exception as e:
        log_message(f"❌ Discord error: {e}")
        return False

def notify_items(items: List[Dict], alert_type: str) -> None:
    """Send notifications for items."""
    if not items:
        return
    
    log_message(f"📤 Sending {len(items)} {alert_type} alerts...")
    
    # Sort by distance score
    items.sort(key=lambda x: x['stats']['score_avg'])
    
    for item in items:
        data = item['data']
        stats = item['stats']
        source = item.get('source', 'unknown')
        
        residence = data.get("residence", {}).get("label", data.get("title", "Unknown"))
        housing_id = data.get("id")
        booking_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{housing_id}"
        
        # Get image if available
        image_url = None
        if data.get("medias"):
            image_url = f"https://trouverunlogement.lescrous.fr/media/{data['medias'][0]['src']}"
        
        # Get price
        try:
            price = f"{data['occupationModes'][0]['rent']['min'] / 100}€"
        except:
            price = "N/A"
        
        # Generate commute links if we have coordinates
        if stats['lat'] and stats['lon']:
            link_work, link_school = generate_commute_links(stats['lat'], stats['lon'])
        else:
            link_work = link_school = "#"
        
        # Determine alert type
        if alert_type == "HIDDEN_GEM":
            title = f"🎯 HIDDEN GEM: {residence}"
            color = COLORS["HIDDEN_GEM"]
            description = f"**⚠️ NOT in search results!**\n[👉 **BOOK NOW**]({booking_url})"
        elif alert_type == "NEW":
            title = f"✨ NEW DROP: {residence}"
            color = COLORS["NEW"]
            description = f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({booking_url})"
        elif alert_type == "WAKE_UP":
            title = f"👻 GHOST WOKE UP: {residence}"
            color = COLORS["WAKE_UP"]
            description = f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({booking_url})"
        elif alert_type == "FORCED":
            title = f"📋 CURRENT: {residence}"
            color = COLORS["FORCED"]
            description = f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({booking_url})"
        else:
            title = f"🏡 FOUND: {residence}"
            color = COLORS["FORCED"]
            description = f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({booking_url})"
        
        # Create fields
        fields = [
            {
                "name": "🏭 Vallourec",
                "value": f"**{stats['dist_work']} km**\n[Route]({link_work})",
                "inline": True
            },
            {
                "name": "🎓 ISTEC",
                "value": f"**{stats['dist_school']} km**\n[Route]({link_school})",
                "inline": True
            },
            {
                "name": "💰 Price",
                "value": f"**{price}**",
                "inline": True
            }
        ]
        
        # Add source info for hidden gems
        if alert_type == "HIDDEN_GEM":
            fields.append({
                "name": "🔍 Source",
                "value": "Direct ID check (not in search)",
                "inline": False
            })
        
        success = send_discord_alert(title, description, color, booking_url, fields, image_url)
        
        if not success:
            log_message(f"⚠️  Failed to send alert for {residence}")

# --- 8. SEARCH LOGIC ---

def fetch_search_results() -> List[Dict]:
    """Fetch visible listings from search API (Source A)."""
    all_results = []
    page = 1
    
    log_message("🔍 Fetching search results...")
    
    while True:
        PAYLOAD["page"] = page
        try:
            response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_header(), timeout=15)
            if response.status_code != 200:
                break
            
            data = response.json()
            items = data.get("results", {}).get("items", [])
            
            if not items:
                break
            
            # Filter for "alone" occupation mode only
            filtered_items = []
            for item in items:
                modes = [m.get("type", "").lower() for m in item.get("occupationModes", [])]
                if "alone" in modes and "house_sharing" not in modes and "couple" not in modes:
                    filtered_items.append({
                        "id": item.get("id"),
                        "data": item,
                        "source": "search"
                    })
            
            all_results.extend(filtered_items)
            
            if len(items) < PAYLOAD["pageSize"]:
                break
                
            page += 1
            time.sleep(0.3)  # Be polite to the server
            
        except Exception as e:
            log_message(f"❌ Search error on page {page}: {e}")
            break
    
    log_message(f"📊 Found {len(all_results)} visible listings")
    return all_results

def fetch_master_list_candidates() -> List[Dict]:
    """Load candidates from master list (Source B)."""
    master_list = load_master_list()
    candidates = []
    
    for item in master_list:
        candidates.append(extract_residence_id_from_master(item))
    
    log_message(f"📋 Master list: {len(candidates)} residences")
    return candidates

# --- 9. MAIN PROCESS ---

async def run_hybrid_check() -> Dict:
    """Main hybrid check combining both sources."""
    log_message("=" * 60)
    log_message("🏠 CROUS V8 HYBRID SNIPER - STARTING")
    log_message("=" * 60)
    
    start_time = time.time()
    
    # 1. Fetch candidates from both sources
    search_candidates = fetch_search_results()
    master_candidates = fetch_master_list_candidates()
    
    # 2. Merge and deduplicate
    all_candidates = []
    seen_ids = set()
    
    # First add search results (Source A - priority)
    for candidate in search_candidates:
        candidate_id = candidate.get("id")
        if candidate_id not in seen_ids:
            seen_ids.add(candidate_id)
            all_candidates.append(candidate)
    
    # Then add master list candidates (Source B - fill in gaps)
    for candidate in master_candidates:
        candidate_id = candidate.get("id")
        if candidate_id not in seen_ids:
            seen_ids.add(candidate_id)
            candidate["source"] = "master_list"
            all_candidates.append(candidate)
    
    log_message(f"📈 Combined: {len(all_candidates)} unique residences")
    log_message(f"   • From search: {len(search_candidates)}")
    log_message(f"   • From master list: {len(master_candidates) - (len(master_candidates) - len(seen_ids))}")
    log_message(f"   • Unique total: {len(all_candidates)}")
    
    # 3. Check availability for all candidates
    if not all_candidates:
        log_message("⚠️  No candidates to check")
        return {"success": False, "audit_results": []}
    
    audit_results = await audit_candidates(all_candidates)
    
    # 4. Analyze results
    available_items = [r for r in audit_results if r['bookable']]
    unavailable_items = [r for r in audit_results if not r['bookable']]
    
    log_message(f"📊 Availability results:")
    log_message(f"   • Available: {len(available_items)}")
    log_message(f"   • Unavailable: {len(unavailable_items)}")
    
    # 5. Track sources of available items
    from_search = [r for r in available_items if r.get('source') == 'search']
    from_master = [r for r in available_items if r.get('source') == 'master_list']
    
    log_message(f"   • From search: {len(from_search)}")
    log_message(f"   • Hidden gems: {len(from_master)}")
    
    duration = time.time() - start_time
    log_message(f"⏱️  Check completed in {duration:.1f} seconds")
    
    return {
        "success": True,
        "audit_results": audit_results,
        "available_items": available_items,
        "unavailable_items": unavailable_items,
        "from_search": from_search,
        "from_master": from_master,
        "duration": duration
    }

def check_crous():
    """Main entry point."""
    log_message("🚀 Starting CROUS V8 Hybrid Sniper")
    
    # Health check
    try:
        health = requests.get(HEALTH_URL, headers=get_header(), timeout=5).json()
        if not health.get("isSystemOnline"):
            log_message("❌ CROUS system is offline")
            if DISCORD_WEBHOOK_URL:
                send_discord_alert("❌ CROUS OFFLINE", 
                                   "The CROUS system appears to be offline.", 
                                   COLORS["ERROR"])
            return
    except:
        log_message("⚠️  Could not check CROUS health status")
    
    # Load current state
    state = load_data()
    
    # Run hybrid check
    try:
        result = asyncio.run(run_hybrid_check())
        
        if not result["success"] or not result["audit_results"]:
            if FORCE_RELIST:
                send_discord_alert("⚠️ EMPTY RESULTS", 
                                   "No listings found in IDF during forced check.", 
                                   COLORS["ERROR"])
            return
        
        # Prepare notifications
        audit_results = result["audit_results"]
        available_items = result["available_items"]
        
        # Identify what's new
        notify_new = []
        notify_wakeup = []
        notify_hidden = []
        notify_forced = []
        
        for item in available_items:
            item_id = item['id']
            
            if FORCE_RELIST:
                notify_forced.append(item)
            else:
                # Check if it was previously a ghost
                if item_id in state["ghosts"]:
                    notify_wakeup.append(item)
                # Check if it's completely new
                elif item_id not in state["all_seen"]:
                    if item.get('source') == 'master_list':
                        notify_hidden.append(item)
                    else:
                        notify_new.append(item)
        
        # Send notifications
        if FORCE_RELIST and notify_forced:
            log_message(f"📢 Force listing {len(notify_forced)} available residences")
            notify_items(notify_forced, "FORCED")
        else:
            if notify_new:
                log_message(f"📢 New drops: {len(notify_new)}")
                notify_items(notify_new, "NEW")
            
            if notify_wakeup:
                log_message(f"📢 Ghosts woke up: {len(notify_wakeup)}")
                notify_items(notify_wakeup, "WAKE_UP")
            
            if notify_hidden:
                log_message(f"🎯 Hidden gems found: {len(notify_hidden)}")
                notify_items(notify_hidden, "HIDDEN_GEM")
                # Update hidden finds counter
                state["hidden_finds"].extend([item['id'] for item in notify_hidden])
                state["stats"]["hidden_gems_found"] += len(notify_hidden)
        
        # Update state
        new_all_seen = []
        new_active = []
        new_ghosts = []
        
        for item in audit_results:
            item_id = item['id']
            new_all_seen.append(item_id)
            
            if item['bookable']:
                new_active.append(item_id)
            else:
                new_ghosts.append(item_id)
        
        # Remove duplicates from hidden_finds
        state["hidden_finds"] = list(set(state["hidden_finds"]))
        
        state["all_seen"] = new_all_seen
        state["active"] = new_active
        state["ghosts"] = new_ghosts
        state["stats"]["total_runs"] = state["stats"].get("total_runs", 0) + 1
        state["stats"]["last_run"] = datetime.now().isoformat()
        
        log_message(f"📊 State updated:")
        log_message(f"   • Active: {len(new_active)}")
        log_message(f"   • Ghosts: {len(new_ghosts)}")
        log_message(f"   • Hidden gems total: {state['stats']['hidden_gems_found']}")
        
        # Save state
        save_data(state)
        
        # Heartbeat (once per day)
        current_time = time.time()
        if not FORCE_RELIST and (current_time - state.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
            summary = f"Active: {len(new_active)} | Ghosts: {len(new_ghosts)} | Hidden gems: {state['stats']['hidden_gems_found']}"
            send_discord_alert("✅ V8 State Summary", summary, COLORS["FORCED"])
            state["last_heartbeat"] = current_time
            save_data(state)
        
        log_message("✅ Check completed successfully")
        
    except Exception as e:
        log_message(f"❌ CRITICAL ERROR: {type(e).__name__} - {str(e)}")
        if DISCORD_WEBHOOK_URL:
            send_discord_alert("❌ BOT ERROR", 
                               f"Error during check: {type(e).__name__}", 
                               COLORS["ERROR"])

if __name__ == "__main__":
    check_crous()