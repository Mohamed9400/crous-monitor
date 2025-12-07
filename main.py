import requests
import json
import os
import time
import random
import urllib.parse
from datetime import datetime, timedelta

# --- 1. CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 

# Your Destination Address
DESTINATION_ADDRESS = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# Search Payload (Ile-de-France)
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "page": 1,
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

# 🥷 STEALTH: Rotate User-Agents
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36'
]

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
HISTORY_FILE = "history.json"
HEARTBEAT_INTERVAL = 86400  # 24 Hours

# --- 2. SMART LINK GENERATOR (The Fix) ---

def generate_commute_link(origin_lat, origin_lon):
    """
    Generates a Universal Google Maps URL.
    - Forces Public Transit (travelmode=transit)
    - Sets Departure Time to the next 7:30 AM (departure_time=TIMESTAMP)
    """
    # 1. Calculate "Next 7:30 AM"
    now = datetime.now()
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    
    # If 7:30 AM has passed today, move to tomorrow
    if target_time < now:
        target_time += timedelta(days=1)
        
    # 2. Convert to Unix Timestamp (Required by Google Maps URL)
    # This gives an integer like 1735626600
    timestamp = int(target_time.timestamp())

    # 3. Encode Destination safely
    dest_encoded = urllib.parse.quote(DESTINATION_ADDRESS)

    # 4. Build the Official Universal Link
    # api=1 -> Forces the map app to handle the parameters
    # origin -> The housing GPS
    # destination -> Vallourec
    # travelmode=transit -> Public Transport
    # departure_time -> The timestamp we calculated
    link = f"https://www.google.com/maps/dir/?api=1&origin={origin_lat},{origin_lon}&destination={dest_encoded}&travelmode=transit&departure_time={timestamp}"
    
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
    default_data = {"ids": [], "last_heartbeat": 0}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list): return {"ids": data, "last_heartbeat": 0}
                return data
        except:
            return default_data
    return default_data

def save_data(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

# --- 4. DISCORD NOTIFICATIONS ---

def send_discord_embed(title, description, color, url=None, fields=None):
    if not DISCORD_WEBHOOK_URL: return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "footer": {"text": f"🤖 CrousBot • {datetime.now().strftime('%H:%M')}"}
    }
    if url: embed["url"] = url
    if fields: embed["fields"] = fields

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        time.sleep(1)
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def notify_new_housing(housing_list):
    print(f"🚀 Sending alerts for {len(housing_list)} rooms...")
    tool_id = PAYLOAD["idTool"]

    for housing in housing_list:
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        room_type = housing.get("label", "Logement")
        housing_id = housing.get("id")
        
        # CROUS Link
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/{tool_id}/accommodations/{housing_id}"
        
        # Commute Link (Fixed)
        try:
            loc = housing.get("location") or housing.get("residence", {}).get("location")
            lat = loc.get("lat")
            lon = loc.get("lon")
            maps_link = generate_commute_link(lat, lon)
            commute_text = f"[🚆 **Check 7:30 AM Route**]({maps_link})"
        except:
            commute_text = "📍 Location unknown"

        # Price
        try:
            price_cents = housing.get("occupationModes", [{}])[0].get("rent", {}).get("min", 0)
            price = f"{price_cents / 100}€"
        except:
            price = "N/A"

        # Area
        try:
            area = housing.get("area", {}).get("min", "?")
            area_str = f"{area} m²"
        except:
            area_str = "N/A"

        # Fields
        fields = [
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "📏 Area", "value": area_str, "inline": True},
            {"name": "🗺️ Commute", "value": commute_text, "inline": False}
        ]
        
        send_discord_embed(
            title=f"🏡 FOUND: {residence}",
            description=f"**Type:** {room_type}\n[👉 Open Listing on CROUS]({crous_url})",
            color=5763719, # Green
            url=crous_url,
            fields=fields
        )

def notify_error(status_code, error_msg):
    send_discord_embed(
        title="⚠️ MONITOR ERROR",
        description=f"Error scanning CROUS.\nCode: {status_code}\nMsg: {error_msg}",
        color=15548997 # Red
    )

def notify_heartbeat(count_ids):
    send_discord_embed(
        title="✅ System Healthy",
        description=f"Scanning active.\nTotal Listings Tracked: **{count_ids}**",
        color=3447003 # Blue
    )

# --- 5. MAIN LOGIC ---

def check_crous():
    print("--- STARTING CHECK ---")
    time.sleep(random.uniform(2, 5)) # Stealth

    data_store = load_data()
    seen_ids = data_store["ids"]
    last_heartbeat = data_store["last_heartbeat"]
    
    print(f"Loaded {len(seen_ids)} known IDs.")

    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header())
        
        if response.status_code != 200:
            notify_error(response.status_code, response.text[:200])
            return

        data = response.json()
        results = data.get("results", {}).get("items", [])
        
        new_items = []
        current_ids = []

        for item in results:
            item_id = item.get("id")
            current_ids.append(item_id)
            if item_id not in seen_ids:
                new_items.append(item)

        if len(new_items) > 0:
            print(f"✅ Found {len(new_items)} NEW listings!")
            notify_new_housing(new_items)
            updated_ids = list(set(seen_ids + current_ids))
            save_data({"ids": updated_ids, "last_heartbeat": last_heartbeat})
        else:
            print("No new listings.")
            now = time.time()
            if (now - last_heartbeat) > HEARTBEAT_INTERVAL:
                notify_heartbeat(len(seen_ids))
                last_heartbeat = now
            save_data({"ids": list(set(seen_ids + current_ids)), "last_heartbeat": last_heartbeat})

    except Exception as e:
        print(f"Script crashed: {e}")
        notify_error(500, str(e))

if __name__ == "__main__":
    check_crous()