import requests
import json
import os
import time
import random
import urllib.parse
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 

# Destination: Vallourec Meudon
DESTINATION_ADDRESS = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 📍 YOUR CUSTOM ZONE (Paris + Petite Couronne)
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "page": 1,
  "pageSize": 24,
  "sector": None,
  "occupationModes": ["alone"],
  "location": [
    # Top Left (North-West) - based on your values
    { "lon": 2.115307, "lat": 49.011465 }, 
    # Bottom Right (South-East) - based on your values
    { "lon": 2.571735, "lat": 48.711189 }  
  ],
  "residence": None,
  "precision": 4,
  "equipment": [],
  "adaptedPmr": False,
  "toolMechanism": "flow"
}

# 🥷 STEALTH SETTINGS
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
]

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
HISTORY_FILE = "history.json"
HEARTBEAT_INTERVAL = 86400  # 24 Hours

# --- 2. LINK GENERATOR (Standard & Reliable) ---

def generate_commute_link(origin_lat, origin_lon):
    """
    Generates a Standard Google Maps Link.
    Forces Public Transit + 7:30 AM Departure.
    """
    now = datetime.now()
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    
    # If 7:30 AM passed, move to tomorrow
    if target_time < now:
        target_time += timedelta(days=1)
        
    # Standard format: YYYY-MM-DD
    date_str = target_time.strftime("%Y-%m-%d") 
    
    dest_encoded = urllib.parse.quote(DESTINATION_ADDRESS)
    
    # Use the reliable "maps.google.com" standard query
    link = (
        f"https://www.google.com/maps?"
        f"saddr={origin_lat},{origin_lon}"
        f"&daddr={dest_encoded}"
        f"&dirflg=r"     # Public Transit
        f"&ttype=dep"    # Departure
        f"&date={date_str}"
        f"&time=07:30"
    )
    
    return link

# --- 3. HELPER FUNCTIONS ---

def get_random_header():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://trouverunlogement.lescrous.fr',
        'Referer': 'https://trouverunlogement.lescrous.fr/',
        'Content-Type': 'application/json'
    }

def load_data():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list): return {"ids": data, "last_heartbeat": 0}
                return data
        except: pass
    return {"ids": [], "last_heartbeat": 0}

def save_data(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

# --- 4. DISCORD NOTIFICATIONS ---

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
    except Exception as e:
        print(f"Failed to send Discord: {e}")

def notify_new_housing(housing_list):
    print(f"🚀 Sending alerts for {len(housing_list)} rooms...")
    
    for housing in housing_list:
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        room_type = housing.get("label", "Logement")
        h_id = housing.get("id")
        
        # CROUS Link
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        # Commute Link
        try:
            loc = housing.get("location") or housing.get("residence", {}).get("location")
            lat = loc.get("lat")
            lon = loc.get("lon")
            maps_link = generate_commute_link(lat, lon)
            commute_text = f"[🚆 **Check Route (7:30 AM)**]({maps_link})"
        except:
            commute_text = "📍 Location unknown"

        # Price & Area
        try:
            price = f"{housing['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"
        
        try:
            area = f"{housing['area']['min']} m²"
        except: area = "N/A"

        fields = [
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "📏 Area", "value": area, "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        
        send_discord_embed(
            title=f"🏡 FOUND: {residence}",
            description=f"**Type:** {room_type}\n[👉 Click to Open CROUS]({crous_url})",
            color=5763719, # Green
            url=crous_url,
            fields=fields
        )

def check_crous():
    print("--- STARTING CHECK ---")
    time.sleep(random.uniform(2, 5))

    data = load_data()
    seen_ids = data["ids"]

    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header())
        if response.status_code != 200:
            send_discord_embed("⚠️ API ERROR", f"Status: {response.status_code}", 15548997)
            return

        items = response.json().get("results", {}).get("items", [])
        
        new_items = []
        current_ids = []
        for item in items:
            h_id = item.get("id")
            current_ids.append(h_id)
            if h_id not in seen_ids:
                new_items.append(item)

        if new_items:
            notify_new_housing(new_items)
            
        # Update History
        data["ids"] = list(set(seen_ids + current_ids))
        
        # Daily Heartbeat
        if (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
            send_discord_embed("✅ System Active", f"Scanning Custom Paris Zone.\nTracking {len(data['ids'])} listings.", 3447003)
            data["last_heartbeat"] = time.time()
            
        save_data(data)

    except Exception as e:
        print(f"Error: {e}")
        send_discord_embed("⚠️ SCRIPT CRASH", str(e), 15548997)

if __name__ == "__main__":
    check_crous()