import requests
import json
import os
import time

# --- 1. SEARCH CONFIGURATION ---
SEARCH_URL = "https://trouverunlogement.lescrous.fr/api/fr/search/42" 

# Your Working Payload (from your last message)
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

# --- 2. DISCORD SETUP ---
# It tries to get the secret from GitHub, or uses a placeholder for local testing
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1447334660756471948/LQ917jr04ZmwRdLYcOvtOrNLgvaxlfgsY-7QQVyCGN3DOSbjgGfBEVbb0gWQ1CGVLYz7"

# UNCOMMENT THE NEXT LINE FOR LOCAL TESTING ONLY (Paste your webhook url)
# DISCORD_WEBHOOK_URL = "YOUR_DISCORD_WEBHOOK_HERE"

def send_discord_alert(housing_list):
    if not DISCORD_WEBHOOK_URL:
        print("No Discord Webhook found. Check your Secrets.")
        return

    print(f"🚀 Sending alerts for {len(housing_list)} rooms...")

    for housing in housing_list:
        # 1. Extract Title (Residence Name + Room Type)
        residence = housing.get("residence", {}).get("label", "Unknown Residence")
        room_type = housing.get("label", "Logement")
        title = f"{residence} - {room_type}"

        # 2. Extract Price (Price is usually in cents, e.g. 26000 = 260.00)
        try:
            price_cents = housing.get("occupationModes", [{}])[0].get("rent", {}).get("min", 0)
            price = f"{price_cents / 100}€"
        except:
            price = "Price Unknown"

        # 3. Extract ID and Link
        housing_id = housing.get("id")
        url = f"https://trouverunlogement.lescrous.fr/tools/{PAYLOAD["idTool"]}/accommodations/{housing_id}"

        # 4. Create Message
        data = {
            "content": f"🚨 **CROUS ALERT!** 🚨\n**{title}**\n💰 Price: {price}\n📍 [Click here to view]({url})"
        }
        
        try:
            requests.post(DISCORD_WEBHOOK_URL, json=data)
            time.sleep(1) # Wait 1 second between messages to be safe
        except Exception as e:
            print(f"Failed to send Discord alert: {e}")

def check_crous():
    print(f"Checking URL: {SEARCH_URL}")
    
    try:
        response = requests.post(SEARCH_URL, json=PAYLOAD, headers=HEADERS)
        
        if response.status_code != 200:
            print(f"❌ Error {response.status_code}: {response.text[:200]}")
            return

        data = response.json()
        
        # --- CORRECT EXTRACTION LOGIC ---
        # Based on the JSON you provided: data['results']['items']
        results = data.get("results", {}).get("items", [])
        
        if len(results) > 0:
            print(f"✅ FOUND {len(results)} LISTINGS! Sending alerts...")
            send_discord_alert(results)
        else:
            print("No listings found (List is empty).")
            
    except Exception as e:
        print(f"Script crashed: {e}")

if __name__ == "__main__":
    check_crous()
