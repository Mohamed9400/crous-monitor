import asyncio
import aiohttp
import requests
import json
import os
import time
import random
import urllib.parse
import math
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 
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

# 📍 STRICT IDF ZONE (Your Coordinates)
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "pageSize": 24,
  "sector": None,
  "occupationModes": ["alone"], 
  "location": [
    { "lon": 1.4462445, "lat": 49.241431 }, 
    { "lon": 3.5592208, "lat": 48.1201456 }
  ],
  "residence": None,
  "precision": 4,
  "equipment": [],
  "adaptedPmr": False,
  "toolMechanism": "flow"
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
]

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
HISTORY_FILE = "history.json"
HEARTBEAT_INTERVAL = 86400
FORCE_RELIST = os.environ.get("FORCE_RELIST", "false").lower() == "true"

# --- 2. MATH & GEOMETRY ---

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

def generate_commute_links(lat, lon):
    now = datetime.now()
    # Set target to tomorrow morning 07:30 to get realistic transit times
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    if target_time < now: target_time += timedelta(days=1)
    date_str = target_time.strftime("%Y-%m-%d") 
    
    def make_link(dest):
        d_enc = urllib.parse.quote(dest)
        # FIXED LINK FORMAT HERE
        return f"https://www.google.com/maps?saddr={lat},{lon}&daddr={d_enc}&dirflg=r&ttype=dep&date={date_str}&time=07:30"

    return make_link(WORK_ADDR), make_link(SCHOOL_ADDR)

# --- 3. ASYNC CORE LOGIC ---

def get_header():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'application/json',
        'Referer': 'https://trouverunlogement.lescrous.fr/'
    }

async def check_availability_async(session, housing_id, semaphore):
    """
    Async Ghost Buster. 
    Returns True if 'periodsAvailable' has data. False otherwise.
    """
    async with semaphore:
        today = datetime.now().strftime("%Y-%m-%d")
        next_year = datetime.now().year + 1
        end_date = f"{next_year}-08-31"
        
        url = f"https://trouverunlogement.lescrous.fr/api/fr/tools/42/accommodations/{housing_id}/availabilities"
        params = {
            "occupationMode": "alone",
            "arrivalDate": today,
            "departureDate": end_date
        }
        
        try:
            async with session.get(url, params=params, headers=get_header(), timeout=8) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("periodsAvailable"):
                        return True
        except:
            return False # Assume Ghost on error to be safe
            
        return False

# --- 4. DATA MANAGEMENT ---

def load_data():
    # Schema: all_seen (Everything currently on site), active (Bookable), ghosts (Unbookable)
    default = {"all_seen": [], "active": [], "ghosts": [], "last_heartbeat": 0}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                # Migration logic if upgrading from V6
                if "all_seen" not in data:
                    return {
                        "all_seen": data.get("ids", []) + data.get("ghost_ids", []),
                        "active": data.get("ids", []),
                        "ghosts": data.get("ghost_ids", []),
                        "last_heartbeat": 0
                    }
                return data
        except: pass
    return default

def save_data(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

def send_discord_embed(title, description, color, url=None, fields=None, image=None):
    if not DISCORD_WEBHOOK_URL: return
    embed = {
        "title": title, "description": description, "color": color,
        "footer": {"text": f"🤖 CrousBot V7.1 • {datetime.now().strftime('%H:%M')}"}
    }
    if url: embed["url"] = url
    if fields: embed["fields"] = fields
    if image: embed["thumbnail"] = {"url": image}
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        time.sleep(0.5)
    except: pass

def notify_items(items, alert_type):
    """
    Sends alerts only for items entering the 'Active' list.
    """
    print(f"🚀 Sending {len(items)} alerts ({alert_type})...")
    
    if FORCE_RELIST and alert_type == "FORCED":
         send_discord_embed("🔄 FORCED REFRESH", f"Displaying **{len(items)}** currently bookable listings.", 3447003)

    for item in items:
        h = item['data']
        stats = item['stats']
        
        residence = h.get("residence", {}).get("label", "Unknown")
        h_id = h.get("id")
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        img_url = None
        if h.get("medias"):
            img_url = f"https://trouverunlogement.lescrous.fr/media/{h['medias'][0]['src']}"

        try: price = f"{h['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"

        link_work, link_school = generate_commute_links(stats['lat'], stats['lon'])

        # Dynamic Titles
        if alert_type == "WAKE_UP":
            title = f"👻 GHOST WOKE UP: {residence}"
            color = 15105570 # Orange
        elif alert_type == "NEW":
            title = f"✨ NEW DROP: {residence}"
            color = 5763719 # Green
        else:
            title = f"🏡 FOUND: {residence}"
            color = 3447003 # Blue
        
        fields = [
            {"name": "🏭 Vallourec", "value": f"**{stats['dist_work']} km**\n[Route]({link_work})", "inline": True},
            {"name": "🎓 ISTEC", "value": f"**{stats['dist_school']} km**\n[Route]({link_school})", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True}
        ]
        
        desc = f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({crous_url})"
        send_discord_embed(title, desc, color, crous_url, fields, img_url)

# --- 5. MAIN PROCESS ---

def fetch_all_pages_sync():
    """Download EVERYTHING on the map."""
    all_results = []
    page = 1
    while True:
        print(f"📡 Fetching Page {page}...")
        PAYLOAD["page"] = page
        try:
            r = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_header(), timeout=15)
            if r.status_code != 200: break
            items = r.json().get("results", {}).get("items", [])
            if not items: break
            all_results.extend(items)
            if len(items) < PAYLOAD["pageSize"]: break
            page += 1
            time.sleep(0.5)
        except: break
    return all_results

async def audit_candidates(candidates):
    """
    Check availability for a list of items.
    Returns the items enriched with 'bookable' boolean and 'stats'.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    results = []
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for item in candidates:
            h_id = item.get("id")
            tasks.append((item, check_availability_async(session, h_id, semaphore)))
        
        print(f"⚡ Auditing {len(tasks)} items...")
        audit_results = await asyncio.gather(*[t[1] for t in tasks])
        
        for i, is_bookable in enumerate(audit_results):
            item = tasks[i][0]
            try:
                loc = item.get("location") or item.get("residence", {}).get("location")
                lat, lon = loc.get("lat"), loc.get("lon")
                d_work = calculate_distance(lat, lon, WORK_LAT, WORK_LON)
                d_school = calculate_distance(lat, lon, SCHOOL_LAT, SCHOOL_LON)
                
                results.append({
                    'id': item.get("id"),
                    'data': item,
                    'bookable': is_bookable,
                    'stats': {
                        'lat': lat, 'lon': lon,
                        'dist_work': d_work,
                        'dist_school': d_school,
                        'score_avg': round((d_work + d_school) / 2, 2)
                    }
                })
            except: pass
            
    return results

def check_crous():
    print(f"--- STARTING V7.1 RECONCILIATION (FORCE={FORCE_RELIST}) ---")
    
    # 1. Health Check
    try:
        if not requests.get(HEALTH_URL, headers=get_header(), timeout=5).json().get("isSystemOnline"):
            print("❌ CROUS DOWN. Sleeping.")
            return
    except: pass

    # 2. Load History (The State)
    state = load_data()
    
    # 3. Fetch "Current Reality"
    raw_items = fetch_all_pages_sync()
    if not raw_items:
        if FORCE_RELIST: send_discord_embed("⚠️ EMPTY", "No listings found in IDF.", 15548997)
        return

    # 4. Filter Surface Level (Only "Alone")
    current_on_site = []
    for item in raw_items:
        modes = [m.get("type", "").lower() for m in item.get("occupationModes", [])]
        if "alone" in modes and "house_sharing" not in modes and "couple" not in modes:
            current_on_site.append(item)

    # 5. Audit EVERYONE currently on site (The Re-Check)
    audit_results = asyncio.run(audit_candidates(current_on_site))
    
    # 6. Sorting Hat (The Diffing Engine)
    next_all_seen = []
    next_active = []
    next_ghosts = []
    
    notify_new = []
    notify_wakeup = []
    notify_forced = []

    for res in audit_results:
        h_id = res['id']
        is_bookable = res['bookable']
        
        # Add to Master List
        next_all_seen.append(h_id)
        
        # Determine Status
        if is_bookable:
            next_active.append(h_id)
            if FORCE_RELIST:
                notify_forced.append(res)
            elif h_id in state["ghosts"]:
                notify_wakeup.append(res) # Ghost -> Active
            elif h_id not in state["all_seen"]:
                notify_new.append(res)    # New -> Active
        else:
            next_ghosts.append(h_id)
            
    # 7. Fire Notifications
    if FORCE_RELIST:
        notify_forced.sort(key=lambda x: x['stats']['score_avg'])
        notify_items(notify_forced, "FORCED")
    else:
        if notify_wakeup:
            notify_wakeup.sort(key=lambda x: x['stats']['score_avg'])
            notify_items(notify_wakeup, "WAKE_UP")
        
        if notify_new:
            notify_new.sort(key=lambda x: x['stats']['score_avg'])
            notify_items(notify_new, "NEW")

    # 8. Save New State
    state["all_seen"] = next_all_seen
    state["active"] = next_active
    state["ghosts"] = next_ghosts
    
    print(f"📊 State Update: {len(next_active)} Active | {len(next_ghosts)} Ghosts | {len(next_all_seen)} Total")
    
    if not FORCE_RELIST and (time.time() - state.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
        send_discord_embed("✅ V7 State", f"Active: {len(next_active)}\nGhosts: {len(next_ghosts)}", 3447003)
        state["last_heartbeat"] = time.time()
        
    save_data(state)

if __name__ == "__main__":
    check_crous()