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

# 🏭 TARGET 1: Vallourec Meudon (For SORTING & DISPLAY)
# We want to see the closest homes to work first.
WORK_LAT = 48.8207
WORK_LON = 2.2337
DESTINATION_ADDRESS = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 🏙️ TARGET 2: Châtelet (For FILTERING)
# We still reject homes too far from the center of Paris.
FILTER_LAT = 48.8606
FILTER_LON = 2.3476
MAX_DISTANCE_FROM_CHATELET = 13.0 

# 🚫 BLACKLIST
BLACKLIST_KEYWORDS = ["colocation","Colocation", "coloc", "partagé", "double", "couple", "rotative", "court séjour"]

# 📍 SEARCH ZONE
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "page": 1,
  "pageSize": 24, # Request 24 items
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

# Check if user requested a Force Relist via GitHub Actions
# Inputs come as strings "true"/"false"
FORCE_RELIST = os.environ.get("FORCE_RELIST", "false").lower() == "true"

# --- 2. MATH & LOGIC ---

def calculate_distance(lat1, lon1, lat2, lon2):
    """Calculates km distance between two points."""
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
    # 1. Check Distance to Chatelet (Keep it central)
    try:
        loc = item.get("location") or item.get("residence", {}).get("location")
        dist_chatelet = calculate_distance(loc.get("lat"), loc.get("lon"), FILTER_LAT, FILTER_LON)
        if dist_chatelet > MAX_DISTANCE_FROM_CHATELET:
            return False
    except: pass

    # 2. Check Blacklist
    text_corpus = (
        item.get("label", "") + " " + 
        item.get("residence", {}).get("label", "")
    ).lower()
    
    for word in BLACKLIST_KEYWORDS:
        if word in text_corpus:
            return False
            
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
        dist_work = item['dist_work'] # Distance to VALLOUREC
        
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

        # Emoji ranking
        rank = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🏠"
        
        fields = [
            {"name": "🏭 Dist. Vallourec", "value": f"**{dist_work} km**", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        send_discord_embed(f"{rank} FOUND: {residence}", f"[👉 Open Listing]({crous_url})", 5763719, crous_url, fields)

# --- 4. MAIN ---

def check_crous():
    print(f"--- STARTING CHECK (FORCE_RELIST={FORCE_RELIST}) ---")
    time.sleep(random.uniform(2, 5))

    data = load_data()
    
    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header(), timeout=15)
        
        if response.status_code != 200:
            if data["status"] == "online":
                send_discord_embed("⚠️ CROUS DOWN", f"HTTP {response.status_code}.", 15548997)
                data["status"] = "offline"
                save_data(data)
            return

        if data["status"] == "offline":
            send_discord_embed("🟢 RECOVERED", "Back online.", 5763719)
            data["status"] = "online"

        items = response.json().get("results", {}).get("items", [])
        
        valid_batch = []
        current_ids = []

        for item in items:
            h_id = item.get("id")
            current_ids.append(h_id)
            
            # If standard run, skip known IDs. 
            # If FORCE_RELIST is True, we process EVERYTHING.
            if not FORCE_RELIST and h_id in data["ids"]: 
                continue

            if is_valid_listing(item):
                # Calculate distance to WORK (Vallourec) for sorting
                try:
                    loc = item.get("location") or item.get("residence", {}).get("location")
                    dist_work = calculate_distance(loc.get("lat"), loc.get("lon"), WORK_LAT, WORK_LON)
                except: dist_work = 999
                
                valid_batch.append({'data': item, 'dist_work': dist_work})

        if valid_batch:
            # Sort by distance to VALLOUREC (Closest first)
            valid_batch.sort(key=lambda x: x['dist_work'])
            notify_batch(valid_batch)
            
        # Update history
        data["ids"] = list(set(data["ids"] + current_ids))
        
        # Only send heartbeat if we didn't just spam a forced relist
        if not FORCE_RELIST and (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
            send_discord_embed("✅ Active", f"Scanning... Tracking {len(data['ids'])} listings.", 3447003)
            data["last_heartbeat"] = time.time()
            
        save_data(data)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_crous()