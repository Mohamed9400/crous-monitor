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

# ⚡ CONCURRENCY LIMIT (Speed vs Safety)
# 10 simultaneous checks is fast but polite.
MAX_CONCURRENT_REQUESTS = 10 

# 📍 STRICT IDF ZONE (Your Custom Bounding Box)
# This removes Reims, Normandy, etc.
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "pageSize": 24,
  "sector": None,
  "occupationModes": ["alone"], 
  "location": [
    { "lon": 1.4462445, "lat": 49.241431 }, # Top Left (West/North)
    { "lon": 3.5592208, "lat": 48.1201456 } # Bottom Right (East/South)
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
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    if target_time < now: target_time += timedelta(days=1)
    date_str = target_time.strftime("%Y-%m-%d") 
    
    def make_link(dest):
        d_enc = urllib.parse.quote(dest)
        return f"http://googleusercontent.com/maps.google.com/maps?saddr={lat},{lon}&daddr={d_enc}&dirflg=r&ttype=dep&date={date_str}&time=07:30"

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
    Uses a semaphore to limit how many requests run at once.
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
            return True # Fail-open: If API lags, don't delete valid housing.
            
        return False

# --- 4. DATA MANAGEMENT ---

def load_data():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {"ids": [], "last_heartbeat": 0, "status": "online"}

def save_data(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

def send_discord_embed(title, description, color, url=None, fields=None, image=None):
    if not DISCORD_WEBHOOK_URL: return
    embed = {
        "title": title, "description": description, "color": color,
        "footer": {"text": f"🤖 CrousBot V5 (Turbo) • {datetime.now().strftime('%H:%M')}"}
    }
    if url: embed["url"] = url
    if fields: embed["fields"] = fields
    if image: embed["thumbnail"] = {"url": image}
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        time.sleep(0.5)
    except: pass

def notify_batch(sorted_list):
    print(f"🚀 Sending alerts for {len(sorted_list)} verified rooms...")
    
    if FORCE_RELIST:
         send_discord_embed("🔄 FORCED REFRESH", f"Found **{len(sorted_list)}** bookable listings in IDF (Ghosts removed).", 3447003)

    for i, item in enumerate(sorted_list):
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

        rank = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🏠"
        
        fields = [
            {"name": "🏭 Vallourec", "value": f"**{stats['dist_work']} km**\n[Route]({link_work})", "inline": True},
            {"name": "🎓 ISTEC", "value": f"**{stats['dist_school']} km**\n[Route]({link_school})", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True}
        ]
        
        send_discord_embed(f"{rank} {residence}", f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({crous_url})", 5763719, crous_url, fields, img_url)

# --- 5. MAIN ASYNC PROCESS ---

def fetch_all_pages_sync():
    """Fetches list sync (fast enough for pagination)."""
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

async def main_async_audit(candidates):
    """Parallel Audit Engine."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    valid_results = []
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        # Create audit tasks for all candidates
        for item in candidates:
            h_id = item.get("id")
            tasks.append((item, check_availability_async(session, h_id, semaphore)))
        
        print(f"⚡ Turbo-Auditing {len(tasks)} items...")
        
        # Run them all at once
        results = await asyncio.gather(*[t[1] for t in tasks])
        
        # Process results
        for i, is_available in enumerate(results):
            item = tasks[i][0]
            if is_available:
                try:
                    loc = item.get("location") or item.get("residence", {}).get("location")
                    lat, lon = loc.get("lat"), loc.get("lon")
                    
                    d_work = calculate_distance(lat, lon, WORK_LAT, WORK_LON)
                    d_school = calculate_distance(lat, lon, SCHOOL_LAT, SCHOOL_LON)
                    avg_score = round((d_work + d_school) / 2, 2)
                    
                    valid_results.append({
                        'data': item,
                        'stats': {
                            'lat': lat, 'lon': lon,
                            'dist_work': d_work,
                            'dist_school': d_school,
                            'score_avg': avg_score
                        }
                    })
                except: pass
            else:
                print(f"👻 Ghost killed: {item.get('id')}")

    return valid_results

def check_crous():
    print(f"--- STARTING V5 TURBO (FORCE={FORCE_RELIST}) ---")
    
    # 1. Health Check
    try:
        if not requests.get(HEALTH_URL, headers=get_header(), timeout=5).json().get("isSystemOnline"):
            print("❌ CROUS DOWN. Sleeping.")
            return
    except: pass

    data = load_data()
    raw_items = fetch_all_pages_sync()
    
    if not raw_items:
        if FORCE_RELIST: send_discord_embed("⚠️ EMPTY", "No listings in IDF.", 15548997)
        return

    # 2. Filter Candidates (Candidates = New IDs OR Force Relist)
    candidates = []
    current_ids = []
    
    for item in raw_items:
        h_id = item.get("id")
        current_ids.append(h_id) # We track everything we see for history
        
        # Filter Logic: Must be "Alone" + (New ID OR Force Mode)
        modes = [m.get("type", "").lower() for m in item.get("occupationModes", [])]
        if "alone" in modes and "house_sharing" not in modes and "couple" not in modes:
            if FORCE_RELIST or (h_id not in data["ids"]):
                candidates.append(item)

    # 3. Async Audit
    if candidates:
        verified_batch = asyncio.run(main_async_audit(candidates))
        
        if verified_batch:
            verified_batch.sort(key=lambda x: x['stats']['score_avg'])
            notify_batch(verified_batch)
        elif FORCE_RELIST:
            send_discord_embed("🚫 NO RESULTS", "Listings found but rejected by Ghost Filter.", 15105570)
            
    # 4. Save State
    print(f"🔄 Snapshot: Tracking {len(current_ids)} IDs.")
    data["ids"] = current_ids
    if not FORCE_RELIST and (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
        send_discord_embed("✅ Active", f"V5 Turbo Active.\nTracking {len(data['ids'])} items.", 3447003)
        data["last_heartbeat"] = time.time()
        
    save_data(data)

if __name__ == "__main__":
    check_crous()