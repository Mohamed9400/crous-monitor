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

# 🏭 TARGET 1: Vallourec Meudon (For SORTING)
WORK_LAT = 48.8207
WORK_LON = 2.2337
DESTINATION_ADDRESS = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 🏙️ TARGET 2: Châtelet (For FILTERING)
FILTER_LAT = 48.8606
FILTER_LON = 2.3476
MAX_DISTANCE_FROM_CHATELET = 13.0 

# 🚫 TEXT BLACKLIST (For Title/Description)
BLACKLIST_KEYWORDS = [
    "colocation", "coloc", "co-location", 
    "partagé", "partager", "partage", "cohabitation",
    "double", "couple", "duo", "conjoint",
    "rotative", "court séjour", "chambre"
]

# 📍 SEARCH ZONE
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  # "page" will be set dynamically in the loop
  "pageSize": 24,
  "sector": None,
  "occupationModes": ["alone"], 
  "location": [
    { "lon": 2.115307, "lat": 49.011465 }, 
    { "lon": 2.571735, "lat": 48.711189 }  
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

# --- 2. MATH & LOGIC ---

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371 # Earth radius
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

def generate_commute_link(origin_lat, origin_lon):
    now = datetime.now()
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    if target_time < now: target_time += timedelta(days=1)
    date_str = target_time.strftime("%Y-%m-%d") 
    dest_encoded = urllib.parse.quote(DESTINATION_ADDRESS)
    return (
        f"https://www.google.com/maps?"
        f"saddr={origin_lat},{origin_lon}&daddr={dest_encoded}"
        f"&dirflg=r&ttype=dep&date={date_str}&time=07:30"
    )

def is_valid_listing(item):
    # 1. STRICT MODE CHECK (The fix you found)
    # If the system says "house_sharing" or "couple", we kill it immediately.
    modes = item.get("occupationModes", [])
    for mode in modes:
        mode_type = mode.get("type", "").lower()
        if mode_type != "alone":
            return False # REJECTED

    # 2. DISTANCE CHECK (Chatelet Filter)
    try:
        loc = item.get("location") or item.get("residence", {}).get("location")
        dist_chatelet = calculate_distance(loc.get("lat"), loc.get("lon"), FILTER_LAT, FILTER_LON)
        if dist_chatelet > MAX_DISTANCE_FROM_CHATELET:
            return False # REJECTED
    except: pass

    # 3. TEXT BLACKLIST (Backup Check)
    raw_data_str = json.dumps(item).lower()
    for word in BLACKLIST_KEYWORDS:
        if word in raw_data_str:
            return False # REJECTED
            
    return True

# --- 3. STANDARD FUNCTIONS ---

def get_random_header():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://trouverunlogement.lescrous.fr',
        'Referer': 'https://trouverunlogement.lescrous.fr/',
        'Content-Type': 'application/json'
    }

def load_data():
    default = {"ids": [], "last_heartbeat": 0, "status": "online"}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list): return {"ids": data, "last_heartbeat": 0, "status": "online"}
                if "status" not in data: data["status"] = "online"
                return data
        except: pass
    return default

def save_data(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

def send_discord_embed(title, description, color, url=None, fields=None):
    if not DISCORD_WEBHOOK_URL: return
    embed = {
        "title": title, "description": description, "color": color,
        "footer": {"text": f"🤖 CrousBot • {datetime.now().strftime('%H:%M')}"}
    }
    if url: embed["url"] = url
    if fields: embed["fields"] = fields
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        time.sleep(1)
    except: pass

def notify_batch(sorted_housing_list):
    print(f"🚀 Sending alerts for {len(sorted_housing_list)} rooms...")
    
    if FORCE_RELIST:
         send_discord_embed("🔄 FORCED REFRESH", f"Displaying **{len(sorted_housing_list)}** available listings sorted by distance to Vallourec.", 3447003)

    for i, item in enumerate(sorted_housing_list):
        housing = item['data']
        dist_work = item['dist_work'] 
        
        residence = housing.get("residence", {}).get("label", "Unknown")
        h_id = housing.get("id")
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        try:
            loc = housing.get("location") or housing.get("residence", {}).get("location")
            maps_link = generate_commute_link(loc.get("lat"), loc.get("lon"))
            commute_text = f"[🚆 **Check Route**]({maps_link})"
        except: commute_text = "📍 Location unknown"

        try: price = f"{housing['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"
        try: area = f"{housing['area']['min']} m²"
        except: area = "N/A"

        rank = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🏠"
        
        fields = [
            {"name": "🏭 Dist. Vallourec", "value": f"**{dist_work} km**", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        send_discord_embed(f"{rank} FOUND: {residence}", f"[👉 Open Listing]({crous_url})", 5763719, crous_url, fields)

# --- 4. MAIN ---

def fetch_all_pages():
    """Loops through all pages until no items are left."""
    all_results = []
    page = 1
    
    while True:
        print(f"📡 Fetching Page {page}...")
        PAYLOAD["page"] = page
        
        try:
            response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header(), timeout=15)
            if response.status_code != 200:
                print(f"❌ Error on page {page}: {response.status_code}")
                break
                
            data = response.json()
            items = data.get("results", {}).get("items", [])
            
            if not items:
                print("✅ No more items found.")
                break
                
            all_results.extend(items)
            
            # If we got fewer items than requested, it's the last page
            if len(items) < PAYLOAD["pageSize"]:
                break
                
            page += 1
            time.sleep(1) # Be nice to the server
            
        except Exception as e:
            print(f"💥 Crash fetching page {page}: {e}")
            break
            
    return all_results

def check_crous():
    print(f"--- STARTING CHECK (FORCE_RELIST={FORCE_RELIST}) ---")
    time.sleep(random.uniform(2, 5))

    data = load_data()
    
    # --- FETCH ALL PAGES ---
    items = fetch_all_pages()
    
    if not items:
        print("No items found on any page.")
        return

    valid_batch = []
    current_ids = []

    for item in items:
        h_id = item.get("id")
        current_ids.append(h_id)
        
        if not FORCE_RELIST and h_id in data["ids"]: 
            continue

        if is_valid_listing(item):
            try:
                loc = item.get("location") or item.get("residence", {}).get("location")
                dist_work = calculate_distance(loc.get("lat"), loc.get("lon"), WORK_LAT, WORK_LON)
            except: dist_work = 999
            
            valid_batch.append({'data': item, 'dist_work': dist_work})

    if valid_batch:
        valid_batch.sort(key=lambda x: x['dist_work'])
        notify_batch(valid_batch)
        
    data["ids"] = list(set(data["ids"] + current_ids))
    data["status"] = "online"
    
    if not FORCE_RELIST and (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
        send_discord_embed("✅ Active", f"Scanning {len(items)} items across pages.", 3447003)
        data["last_heartbeat"] = time.time()
        
    save_data(data)

if __name__ == "__main__":
    check_crous()