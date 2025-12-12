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

# 🎯 YOUR TARGET: Vallourec Meudon Campus (Approx GPS)
TARGET_LAT = 48.8207
TARGET_LON = 2.2337
DESTINATION_ADDRESS = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 📍 YOUR CUSTOM ZONE
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

# --- 2. MATH: HAVERSINE DISTANCE ---
def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculates distance in km between two GPS points.
    """
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

# --- 3. LINK GENERATOR ---
def generate_commute_link(origin_lat, origin_lon):
    now = datetime.now()
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    if target_time < now: target_time += timedelta(days=1)
    date_str = target_time.strftime("%Y-%m-%d") 
    dest_encoded = urllib.parse.quote(DESTINATION_ADDRESS)
    
    # Official Standard Google Link
    return (
        f"https://www.google.com/maps?"
        f"saddr={origin_lat},{origin_lon}&daddr={dest_encoded}"
        f"&dirflg=r&ttype=dep&date={date_str}&time=07:30"
    )

# --- 4. HELPER FUNCTIONS ---
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

# --- 5. DISCORD NOTIFICATIONS ---
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
    print(f"🚀 Sending alerts for {len(sorted_housing_list)} rooms (Sorted by Distance)...")
    
    for i, item in enumerate(sorted_housing_list):
        housing = item['data']
        distance = item['dist']
        
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        h_id = housing.get("id")
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        # Link Generation
        try:
            loc = housing.get("location") or housing.get("residence", {}).get("location")
            maps_link = generate_commute_link(loc.get("lat"), loc.get("lon"))
            commute_text = f"[🚆 **Check Route (7:30 AM)**]({maps_link})"
        except: commute_text = "📍 Location unknown"

        # Price & Area
        try: price = f"{housing['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"
        try: area = f"{housing['area']['min']} m²"
        except: area = "N/A"

        # Emoji based on rank
        rank_emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🏠"

        fields = [
            {"name": "📏 Distance", "value": f"**{distance} km**", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        
        send_discord_embed(
            f"{rank_emoji} FOUND: {residence}", 
            f"**Distance to Work:** {distance} km\n[👉 Open Listing]({crous_url})", 
            5763719, 
            crous_url, 
            fields
        )

# --- 6. MAIN LOGIC ---
def check_crous():
    print("--- STARTING CHECK ---")
    time.sleep(random.uniform(2, 5))

    data = load_data()
    previous_status = data["status"]
    
    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header(), timeout=15)
        
        if response.status_code != 200:
            if previous_status == "online":
                send_discord_embed("⚠️ CROUS DOWN", f"HTTP {response.status_code}. Silence until recovery.", 15548997)
                data["status"] = "offline"
                save_data(data)
            return

        if previous_status == "offline":
            send_discord_embed("🟢 CROUS RECOVERED", "Back online.", 5763719)
            data["status"] = "online"

        items = response.json().get("results", {}).get("items", [])
        
        # --- NEW SORTING LOGIC ---
        new_batch = []
        current_ids = []

        for item in items:
            h_id = item.get("id")
            current_ids.append(h_id)
            
            if h_id not in data["ids"]:
                # Calculate Distance IMMEDIATELY
                try:
                    loc = item.get("location") or item.get("residence", {}).get("location")
                    dist = calculate_distance(loc.get("lat"), loc.get("lon"), TARGET_LAT, TARGET_LON)
                except:
                    dist = 999 # If unknown, put it last
                
                # Add to batch with distance info
                new_batch.append({'data': item, 'dist': dist})

        if new_batch:
            # SORT by distance (Smallest number first)
            new_batch.sort(key=lambda x: x['dist'])
            notify_batch(new_batch)
            
        data["ids"] = list(set(data["ids"] + current_ids))
        data["status"] = "online"
        
        if (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
            send_discord_embed("✅ System Active", f"Tracking {len(data['ids'])} listings.", 3447003)
            data["last_heartbeat"] = time.time()
            
        save_data(data)

    except Exception as e:
        print(f"Crash: {e}")
        if previous_status == "online":
            send_discord_embed("⚠️ CONNECTION FAILED", "Silence until recovery.", 15548997)
            data["status"] = "offline"
            save_data(data)

if __name__ == "__main__":
    check_crous()