import requests
import json
import os
import time
import random
import urllib.parse
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 
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
HEARTBEAT_INTERVAL = 86400  # 24 Hours

# --- 2. LINK GENERATOR ---
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
    # Default structure now includes "status"
    default = {"ids": [], "last_heartbeat": 0, "status": "online"}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                # Fix old files that don't have "status"
                if isinstance(data, list): return {"ids": data, "last_heartbeat": 0, "status": "online"}
                if "status" not in data: data["status"] = "online"
                return data
        except: pass
    return default

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
    except: pass

def notify_new_housing(housing_list):
    print(f"🚀 Sending alerts for {len(housing_list)} rooms...")
    for housing in housing_list:
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        h_id = housing.get("id")
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        try:
            loc = housing.get("location") or housing.get("residence", {}).get("location")
            maps_link = generate_commute_link(loc.get("lat"), loc.get("lon"))
            commute_text = f"[🚆 **Check Route (7:30 AM)**]({maps_link})"
        except: commute_text = "📍 Location unknown"

        try: price = f"{housing['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"
        
        try: area = f"{housing['area']['min']} m²"
        except: area = "N/A"

        fields = [
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "📏 Area", "value": area, "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        send_discord_embed(f"🏡 FOUND: {residence}", f"[👉 Open Listing]({crous_url})", 5763719, crous_url, fields)

# --- 5. SMART CHECK LOGIC ---
def check_crous():
    print("--- STARTING CHECK ---")
    time.sleep(random.uniform(2, 5))

    data = load_data()
    previous_status = data["status"]
    
    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header(), timeout=15)
        
        # --- SCENARIO A: SITE IS BROKEN (404, 500, 403) ---
        if response.status_code != 200:
            error_msg = f"HTTP {response.status_code}"
            print(f"❌ API Error: {error_msg}")
            
            # Only alert IF it was previously online (Stop Spam)
            if previous_status == "online":
                send_discord_embed("⚠️ CROUS DOWN", f"The API is returning errors ({error_msg}).\n**I will stay silent until it comes back.**", 15548997)
                data["status"] = "offline"
                save_data(data)
            return

        # --- SCENARIO B: SITE IS WORKING ---
        # If it was offline before, tell the user it's back!
        if previous_status == "offline":
            send_discord_embed("🟢 CROUS RECOVERED", "The website is back online! Resuming normal scans.", 5763719)
            data["status"] = "online"
            # Don't return, continue to check housing!

        # Normal Housing Logic
        items = response.json().get("results", {}).get("items", [])
        new_items = [i for i in items if i.get("id") not in data["ids"]]
        current_ids = [i.get("id") for i in items]

        if new_items:
            notify_new_housing(new_items)
            
        # Update Data
        data["ids"] = list(set(data["ids"] + current_ids))
        data["status"] = "online" # Ensure status is set to online
        
        # Heartbeat
        if (time.time() - data.get("last_heartbeat", 0)) > HEARTBEAT_INTERVAL:
            send_discord_embed("✅ System Active", f"Tracking {len(data['ids'])} listings.", 3447003)
            data["last_heartbeat"] = time.time()
            
        save_data(data)

    except Exception as e:
        # --- SCENARIO C: CONNECTION CRASH (Timeout, DNS, etc) ---
        print(f"💥 Crash: {e}")
        # Only alert IF it was previously online
        if previous_status == "online":
            send_discord_embed("⚠️ CONNECTION FAILED", f"Could not reach CROUS server.\n**I will stay silent until it comes back.**", 15548997)
            data["status"] = "offline"
            save_data(data)

if __name__ == "__main__":
    check_crous()