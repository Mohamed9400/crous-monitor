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
HEALTH_URL = "https://trouverunlogement.lescrous.fr/api/health"

# 🏭 TARGET 1: Vallourec Meudon (Work)
WORK_LAT = 48.8207
WORK_LON = 2.2337
WORK_ADDR = "Vallourec Meudon Campus, 12 Rue de la Verrerie, 92190 Meudon"

# 🎓 TARGET 2: ISTEC Paris (School) - 128 Quai de Jemmapes
SCHOOL_LAT = 48.8769
SCHOOL_LON = 2.3655
SCHOOL_ADDR = "ISTEC, 128 Quai de Jemmapes, 75010 Paris"

# 🚫 STRICT TYPE FILTER (System Flags)
# We only accept "alone". "house_sharing" and "couple" are banned.
ACCEPTED_MODES = ["alone"]

# 📍 SEARCH ZONE: WIDE NET (No Coordinates)
# We send an empty location to get ALL of Ile-de-France
PAYLOAD = {
  "idTool": 42,
  "need_aggregation": True,
  "pageSize": 24,
  "sector": None,
  "occupationModes": ["alone"], 
  "location": [], # Empty = Everywhere
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

# --- 2. MATH & GEOMETRY ---

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371 
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return round(R * c, 2)

def generate_commute_links(lat, lon):
    """Generates two Google Maps links: one to Work, one to School."""
    now = datetime.now()
    target_time = now.replace(hour=7, minute=30, second=0, microsecond=0)
    if target_time < now: target_time += timedelta(days=1)
    date_str = target_time.strftime("%Y-%m-%d") 
    
    def make_link(dest):
        d_enc = urllib.parse.quote(dest)
        return f"https://www.google.com/maps?saddr={lat},{lon}&daddr={d_enc}&dirflg=r&ttype=dep&date={date_str}&time=07:30"

    return make_link(WORK_ADDR), make_link(SCHOOL_ADDR)

# --- 3. CORE LOGIC ---

def get_header():
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'application/json',
        'Referer': 'https://trouverunlogement.lescrous.fr/'
    }

def check_site_health():
    """Returns True if CROUS is online, False if broken."""
    try:
        r = requests.get(HEALTH_URL, headers=get_header(), timeout=10)
        data = r.json()
        if data.get("isSystemOnline") and data.get("isMseOnline"):
            return True
    except:
        pass
    return False

def verify_availability(housing_id):
    """
    The Ghost Buster.
    Queries the /availabilities endpoint to see if it's actually bookable.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    # End date is always Next Year's August 31st (End of School Year)
    next_year = datetime.now().year + 1
    end_date = f"{next_year}-08-31"
    
    url = f"https://trouverunlogement.lescrous.fr/api/fr/tools/42/accommodations/{housing_id}/availabilities"
    params = {
        "occupationMode": "alone",
        "arrivalDate": today,
        "departureDate": end_date
    }
    
    try:
        time.sleep(0.4) # Be polite
        r = requests.get(url, params=params, headers=get_header(), timeout=5)
        if r.status_code == 200:
            data = r.json()
            # If "periodsAvailable" is not empty, it's REAL.
            if data.get("periodsAvailable"):
                return True
    except Exception as e:
        print(f"⚠️ Avail Check Failed for {housing_id}: {e}")
        # If API fails, we assume it's valid to be safe (don't delete valid housing due to lag)
        return True 
        
    return False

def is_valid_surface_level(item):
    """Fast filter (No API calls). Checks Type."""
    modes = item.get("occupationModes", [])
    has_alone = False
    
    for mode in modes:
        m_type = mode.get("type", "").lower()
        if m_type == "alone": has_alone = True
        # Immediate Disqualifiers
        if m_type in ["house_sharing", "couple"]: return False
            
    return has_alone

# --- 4. DATA MANAGEMENT ---

def load_data():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except: pass
    return {"ids": [], "last_heartbeat": 0, "status": "online"}

def save_data(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f)

def send_discord_embed(title, description, color, url=None, fields=None, image=None):
    if not DISCORD_WEBHOOK_URL: return
    embed = {
        "title": title, "description": description, "color": color,
        "footer": {"text": f"🤖 CrousBot V4 • {datetime.now().strftime('%H:%M')}"}
    }
    if url: embed["url"] = url
    if fields: embed["fields"] = fields
    if image: embed["thumbnail"] = {"url": image} # Show the room photo!
    
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
        time.sleep(1)
    except: pass

def notify_batch(sorted_list):
    print(f"🚀 Sending alerts for {len(sorted_list)} verified rooms...")
    
    if FORCE_RELIST:
         send_discord_embed("🔄 FORCED REFRESH", f"Found **{len(sorted_list)}** bookable listings (Ghosts removed).", 3447003)

    for i, item in enumerate(sorted_list):
        h = item['data']
        stats = item['stats']
        
        residence = h.get("residence", {}).get("label", "Unknown")
        h_id = h.get("id")
        crous_url = f"https://trouverunlogement.lescrous.fr/tools/42/accommodations/{h_id}"
        
        # Image
        img_url = None
        if h.get("medias"):
            img_url = f"https://trouverunlogement.lescrous.fr/media/{h['medias'][0]['src']}"

        # Price
        try: price = f"{h['occupationModes'][0]['rent']['min'] / 100}€"
        except: price = "N/A"

        # Links
        link_work, link_school = generate_commute_links(stats['lat'], stats['lon'])

        rank = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "🏠"
        
        fields = [
            {"name": "🏭 Vallourec (Work)", "value": f"**{stats['dist_work']} km**\n[route]({link_work})", "inline": True},
            {"name": "🎓 ISTEC (School)", "value": f"**{stats['dist_school']} km**\n[route]({link_school})", "inline": True},
            {"name": "💰 Price", "value": f"**{price}**", "inline": True}
        ]
        
        desc = f"**Score:** {stats['score_avg']} km avg\n[👉 **BOOK NOW**]({crous_url})"
        send_discord_embed(f"{rank} {residence}", desc, 5763719, crous_url, fields, img_url)

# --- 5. MAIN ---

def fetch_all_pages():
    all_results = []
    page = 1
    while True:
        print(f"📡 Fetching Page {page}...")
        PAYLOAD["page"] = page
        try:
            r = requests.post(SEARCH_URL, json=PAYLOAD, headers=get_header(), timeout=15)
            if r.status_code != 200: break
            items = r.json().get("results", {}).get("items", [])
            if not items: break
            all_results.extend(items)
            if len(items) < PAYLOAD["pageSize"]: break
            page += 1
            time.sleep(1)
        except: break
    return all_results

def check_crous():
    print(f"--- STARTING V4 AUDIT (FORCE={FORCE_RELIST}) ---")
    time.sleep(random.uniform(2, 5))

    # 1. HEALTH CHECK
    if not check_site_health():
        print("❌ CROUS System is DOWN. Sleeping.")
        return

    data = load_data()
    items = fetch_all_pages()
    
    if not items:
        if FORCE_RELIST: send_discord_embed("⚠️ EMPTY", "API returned 0 listings.", 15548997)
        return

    valid_batch = []
    current_run_ids = [] 

    for item in items:
        h_id = item.get("id")
        
        # SURFACE FILTER (Fast)
        if not is_valid_surface_level(item):
            continue

        # GHOST BUSTER (Slow - Only check if we need to)
        is_new = h_id not in data["ids"]
        
        # We check availability if:
        # A) It's a brand new ID
        # B) We are forcing a relist (to clear out old ghosts)
        if is_new or FORCE_RELIST:
            print(f"🕵️ Auditing ID {h_id}...")
            if not verify_availability(h_id):
                print(f"👻 Ghost detected: {h_id} (No availability).")
                continue # Skip this fake listing
            
            # If we survived, calculate scores
            current_run_ids.append(h_id)
            try:
                loc = item.get("location") or item.get("residence", {}).get("location")
                lat, lon = loc.get("lat"), loc.get("lon")
                
                d_work = calculate_distance(lat, lon, WORK_LAT, WORK_LON)
                d_school = calculate_distance(lat, lon, SCHOOL_LAT, SCHOOL_LON)
                avg_score = round((d_work + d_school) / 2, 2)
                
                valid_batch.append({
                    'data': item,
                    'stats': {
                        'lat': lat, 'lon': lon,
                        'dist_work': d_work,
                        'dist_school': d_school,
                        'score_avg': avg_score
                    }
                })
            except: pass
        else:
            # If it's old and we aren't forcing, just keep it in memory
            current_run_ids.append(h_id)

    # SORT & NOTIFY
    if valid_batch:
        # Sort by "Average Distance" (Balance Score)
        valid_batch.sort(key=lambda x: x['stats']['score_avg'])
        notify_batch(valid_batch)
    elif FORCE_RELIST:
        send_discord_embed("🚫 NO RESULTS", "0 listings passed the Availability Check.", 15105570)
    
    # SNAPSHOT SAVE
    data["ids"] = current_run_ids
    save_data(data)

if __name__ == "__main__":
    check_crous()