import requests
import json
import os
import time

# --- CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 

# Your Working Payload
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://trouverunlogement.lescrous.fr',
    'Referer': 'https://trouverunlogement.lescrous.fr/',
    'Content-Type': 'application/json'
}

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []

def save_history(ids_list):
    with open(HISTORY_FILE, "w") as f:
        json.dump(ids_list, f)

def send_discord_alert(housing_list):
    if not DISCORD_WEBHOOK_URL:
        return

    print(f"🚀 Sending alerts for {len(housing_list)} NEW rooms...")

    for housing in housing_list:
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        room_type = housing.get("label", "Logement")
        title = f"{residence} - {room_type}"
        
        try:
            price_cents = housing.get("occupationModes", [{}])[0].get("rent", {}).get("min", 0)
            price = f"{price_cents / 100}€"
        except:
            price = "Price Unknown"

        housing_id = housing.get("id")
        url = f"https://trouverunlogement.lescrous.fr/tools/{PAYLOAD["idTool"]}/accommodations/{housing_id}"

        data = {
            "content": f"🚨 **NEW LISTING!** 🚨\n**{title}**\n💰 Price: {price}\n📍 [Click here to view]({url})"
        }
        
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=data)
            time.sleep(1) 
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

def check_crous():
    print("--- STARTING CHECK ---")
    
    # 1. Load the Memory
    seen_ids = load_history()
    print(f"Loaded {len(seen_ids)} previously seen IDs.")

    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=HEADERS)
        if response.status_code != 200:
            print(f"❌ Error {response.status_code}")
            return

        data = response.json()
        results = data.get("results", {}).get("items", [])
        
        # 2. Filter for NEW items only
        new_items = []
        current_ids = []

        for item in results:
            item_id = item.get("id")
            current_ids.append(item_id) # We keep track of everything currently online
            
            # If this ID is NOT in our history, it's new!
            if item_id not in seen_ids:
                new_items.append(item)

        # 3. Handle Results
        if len(new_items) > 0:
            print(f"✅ Found {len(new_items)} NEW listings!")
            send_discord_alert(new_items)
            
            # 4. Update History (Add new IDs to the known list)
            # We combine old history + new finds
            updated_history = list(set(seen_ids + current_ids))
            save_history(updated_history)
            print("History updated.")
        else:
            print("No new listings (duplicates ignored).")
            # We still save current IDs to keep history fresh
            save_history(list(set(seen_ids + current_ids)))

    except Exception as e:
        print(f"Script crashed: {e}")

if __name__ == "__main__":
    check_crous()