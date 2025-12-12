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

# 🏙️ FILTER TARGET: Châtelet - Les Halles (The Transport Hub)
# We filter listings based on their distance to THIS point.
FILTER_LAT = 48.8606
FILTER_LON = 2.3476
MAX_DISTANCE_KM = 13.0  # Covers Saint-Denis (9km), Montreuil, Ivry, etc.

# 🏭 COMMUTE TARGET: Vallourec Meudon (Your Work)
# The "Check Route" button will point here.
DESTINATION_ADDRESS = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 🚫 BLACKLIST (Refined)
# Deleted MIN_SIZE_M2 as requested.
BLACKLIST_KEYWORDS = [
    "colocation", "coloc", "partagé", 
    "double", "couple",
    "rotative", "court séjour"
]

# 📍 YOUR ZONE (Paris + Petite Couronne)
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "page": 1,
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

# --- 2. MATH & LOGIC ---

def calculate_distance_from_chatelet(lat1, lon1):
    """Calculates km distance from Chatelet."""
    R = 6371 # Earth radius
    dlat = math.radians(FILTER_LAT - lat1)
    dlon = math.radians(FILTER_LON - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(FILTER_LAT)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

def generate_commute_link(origin_lat, origin_lon):
    """Generates map link to VALLOUREC MEUDON (Not Chatelet)."""
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

def is_valid_listing(item, dist_from_chatelet):
    # 1. Check Distance to Chatelet
    if dist_from_chatelet > MAX_DISTANCE_KM:
        return False 

    # 2. Check Blacklist Keywords
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
    for i, item in enumerate(sorted_housing_list):
        housing = item['data']
        dist = item['dist'] # This is distance to CHATELET
        
        residence = housing.get("residence", {}).get("label", "Unknown")
        h_id = housing.get("id")
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        # Link to MEUDON
        try:
            loc = housing.get("location") or housing.get("residence", {}).get("location")
            maps_link = generate_commute_link(loc.get("lat"), loc.get("lon"))
            commute_text = f"[🚆 **Check Route to Meudon**]({maps_link})"
        except: commute_text = "📍 Location unknown"

        try: price = f"{housing['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"
        try: area = f"{housing['area']['min']} m²"
        except: area = "N/A"

        rank = "🥇" if i == 0 else "🥈" if i == 1 else "🏠"
        
        fields = [
            {"name": "🗼 Dist. Châtelet", "value": f"**{dist} km**", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        send_discord_embed(f"{rank} FOUND: {residence}", f"[👉 Open Listing]({crous_url})", 5763719, crous_url, fields)

# --- 4. MAIN ---

def check_crous():
    print("--- STARTING CHECK ---")
    time.sleep(random.uniform(2, 5))

    data = load_data()
    first_run = len(data["ids"]) == 0
    
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
        
        new_batch = []
        current_ids = []

        for item in items:
            h_id = item.get("id")
            current_ids.append(h_id)
            
            if h_id in data["ids"]: continue

            # Calc distance to CHATELET
            try:
                loc = item.get("location") or item.get("residence", {}).get("location")
                dist = calculate_distance_from_chatelet(loc.get("lat"), loc.get("lon"))
            except: dist = 999
            
            if is_valid_listing(item, dist):
                new_batch.append({'data': item, 'dist': dist})

        if new_batch:
            new_batch.sort(key=lambda x: x['dist']) # Closest to Chatelet first
            
            if first_run:
                print(f"First run: {len(new_batch)} listings found. Saving silently.")
                send_discord_embed("✅ Bot Initialized", f"Found {len(new_batch)} listings near Châtelet (<{MAX_DISTANCE_KM}km).\nWaiting for new drops...", 3447003)
            else:
                notify_batch(new_batch)
            
        data["ids"] = list(set(data["ids"] + current_ids))
        
        if (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
            send_discord_embed("✅ Active", f"Scanning around Châtelet.\nTracking {len(data['ids'])} listings.", 3447003)
            data["last_heartbeat"] = time.time()
            
        save_data(data)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_crous()