import requests
import json
import os
import time
import random
from datetime import datetime

# --- 1. CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 

# Payload (Standard Paris/Ile-de-France area)
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

# 🥷 STEALTH: List of different browser identities to rotate
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/119.0'
]

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
HISTORY_FILE = "history.json"
HEARTBEAT_INTERVAL = 86400  # 24 Hours in seconds

# --- 2. HELPER FUNCTIONS ---

def get_random_header():
    """Returns headers with a random User-Agent to avoid detection."""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://trouverunlogement.lescrous.fr',
        'Referer': 'https://trouverunlogement.lescrous.fr/',
        'Content-Type': 'application/json'
    }

def load_data():
    """Loads history and last heartbeat time."""
    default_data = {"ids": [], "last_heartbeat": 0}
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                data = json.load(f)
                # Ensure structure is correct if upgrading from old version
                if isinstance(data, list): 
                    return {"ids": data, "last_heartbeat": 0}
                return data
        except:
            return default_data
    return default_data

def save_data(data):
    """Saves history and heartbeat to file."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

# --- 3. DISCORD NOTIFICATIONS ---

def send_discord_embed(title, description, color, url=None, fields=None):
    """Generic function to send a beautiful embed card."""
    if not DISCORD_WEBHOOK_URL:
        return

    embed = {
        "title": title,
        "description": description,
        "color": color, # Decimal color code
        "footer": {"text": f"🤖 Crous Monitor • {datetime.now().strftime('%H:%M:%S')}"}
    }
    
    if url: embed["url"] = url
    if fields: embed["fields"] = fields

    payload = {"embeds": [embed]}
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload)
        time.sleep(1)
    except Exception as e:
        print(f"Failed to send Discord alert: {e}")

def notify_new_housing(housing_list):
    """🎨 Sends a RICH visual card for new housing."""
    print(f"🚀 Sending alerts for {len(housing_list)} NEW rooms...")
    
    tool_id = PAYLOAD["idTool"] # Extract safely for URL

    for housing in housing_list:
        # Extract details
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        room_type = housing.get("label", "Logement")
        housing_id = housing.get("id")
        
        # Build Link
        url = f"https://trouverunlogement.lescrous.fr/tools/{tool_id}/accommodations/{housing_id}"
        
        # Price
        try:
            price_cents = housing.get("occupationModes", [{}])[0].get("rent", {}).get("min", 0)
            price = f"{price_cents / 100}€"
        except:
            price = "N/A"

        # Area (m²)
        try:
            area = housing.get("area", {}).get("min", "?")
            area_str = f"{area} m²"
        except:
            area_str = "N/A"

        # Send Green Embed (Success)
        fields = [
            {"name": "💰 Price", "value": f"**{price}**", "inline": True},
            {"name": "📏 Area", "value": area_str, "inline": True}
        ]
        
        send_discord_embed(
            title=f"🏡 FOUND: {residence}",
            description=f"**Type:** {room_type}\n[👉 Click here to open on CROUS]({url})",
            color=5763719, # Green
            url=url,
            fields=fields
        )

def notify_error(status_code, error_msg):
    """🚨 Sends a RED alert if the script crashes."""
    send_discord_embed(
        title="⚠️ MONITOR ERROR",
        description=f"The bot encountered an error scanning CROUS.\n**Code:** {status_code}\n**Error:** {error_msg}",
        color=15548997 # Red
    )

def notify_heartbeat(count_ids):
    """💓 Sends a BLUE status report once a day."""
    send_discord_embed(
        title="✅ System Healthy",
        description=f"I am still alive and scanning.\n**Total listings tracked:** {count_ids}\nNo new housing found in this scan.",
        color=3447003 # Blue
    )

# --- 4. MAIN LOGIC ---

def check_crous():
    print("--- STARTING CHECK ---")
    
    # 🥷 STEALTH: Wait randomly 2-5 seconds before starting to look human
    time.sleep(random.uniform(2, 5))

    # Load Memory
    data_store = load_data()
    seen_ids = data_store["ids"]
    last_heartbeat = data_store["last_heartbeat"]
    
    print(f"Loaded {len(seen_ids)} known IDs.")

    try:
        # Request with Random Header
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_random_header())
        
        # 🚨 ERROR CHECKING
        if response.status_code != 200:
            print(f"❌ Error {response.status_code}")
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

        # Logic: Listings Found?
        if len(new_items) > 0:
            print(f"✅ Found {len(new_items)} NEW listings!")
            notify_new_housing(new_items)
            
            # Save new history
            updated_ids = list(set(seen_ids + current_ids))
            save_data({"ids": updated_ids, "last_heartbeat": last_heartbeat})
            
        else:
            print("No new listings.")
            
            # 💓 HEARTBEAT CHECK
            # If it has been more than 24 hours (86400 seconds) since last heartbeat
            now = time.time()
            if (now - last_heartbeat) > HEARTBEAT_INTERVAL:
                print("Sending Heartbeat...")
                notify_heartbeat(len(seen_ids))
                last_heartbeat = now # Update time
            
            # Save history (to keep IDs fresh and update heartbeat time)
            save_data({"ids": list(set(seen_ids + current_ids)), "last_heartbeat": last_heartbeat})

    except Exception as e:
        print(f"Script crashed: {e}")
        notify_error(500, str(e))

if __name__ == "__main__":
    check_crous()