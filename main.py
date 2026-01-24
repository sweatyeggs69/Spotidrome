import os
import hashlib
import random
import time
import requests
import json
import schedule
import sys
from datetime import datetime
from google import genai
from google.genai import types

# Ensure logs show up immediately in Docker
sys.stdout.reconfigure(line_buffering=True)

# --- Configuration ---
URL = os.getenv("NAVIDROME_URL")
USER = os.getenv("NAVIDROME_USER")
PASS = os.getenv("NAVIDROME_PASS")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-preview-09-2025")

# Local file to track which playlist ID is our 'daylist'
MAP_FILE = "playlist_map.json"

SYSTEM_INSTRUCTION = """
You are Spotidrome, a professional music curator. You generate personalized music experiences in JSON format.

TASK 1: Daily Mix
- Anchor around 'top_artist_recently'.
- 50 tracks total (40 recent, 10 discovery).
- Output key: "daily_mix" (list of IDs).

TASK 2: daylist
- Create a hyper-personalized mix based on the user's current 'vibe' and time of day.
- Naming: Generate a hyper-specific, all-lowercase title (NO "daylist" prefix).
- The title should mash together descriptors, feelings, and the current time/day. 
- Example titles: "cottagecore goblincore friday evening", "liminal space synthwave monday morning", "core memories sing along friday evening".
- Selection: Pick 50 tracks that fit this specific generated vibe.
- Output keys: "daylist_name" (string) and "daylist_ids" (list of IDs).

CRITICAL: Return ONLY valid JSON.
"""

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def get_auth_params():
    salt = "".join([random.choice("0123456789abcdef") for _ in range(10)])
    token = hashlib.md5((PASS + salt).encode()).hexdigest()
    return {"u": USER, "t": token, "s": salt, "v": "1.16.1", "c": "Spotidrome", "f": "json"}

def call_subsonic(endpoint, extra_params={}):
    params = get_auth_params()
    params.update(extra_params)
    try:
        response = requests.get(f"{URL}/rest/{endpoint}.view", params=params, timeout=15)
        response.raise_for_status()
        return response.json().get("subsonic-response", {})
    except Exception as e:
        log(f"API Error ({endpoint}): {e}")
        return {}

def load_playlist_map():
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_playlist_map(data):
    with open(MAP_FILE, 'w') as f:
        json.dump(data, f)

def fetch_music_data():
    log("Step 1: Analyzing library activity...")
    artist_counts = {}
    recent_pool = []
    seen_ids = set()

    # Get recent albums for 'vibe' context
    recent_data = call_subsonic("getAlbumList", {"type": "recent", "size": 60})
    albums = recent_data.get("albumList", {}).get("album", [])
    if not isinstance(albums, list): albums = [albums] if albums else []

    for alb in albums:
        artist = alb.get('artist', 'Unknown')
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        album_details = call_subsonic("getAlbum", {"id": alb['id']})
        tracks = album_details.get("album", {}).get("song", [])
        if not isinstance(tracks, list): tracks = [tracks] if tracks else []
        for t in tracks:
            if t['id'] not in seen_ids:
                recent_pool.append(t)
                seen_ids.add(t['id'])

    top_artist = max(artist_counts, key=artist_counts.get) if artist_counts else "Various"
    
    # Discovery pool
    discovery_data = call_subsonic("getRandomSongs", {"size": 150})
    discovery = discovery_data.get("randomSongs", {}).get("song", [])

    return {
        "top_artist": top_artist,
        "recent_pool": recent_pool,
        "discovery": discovery,
        "current_time": datetime.now().strftime("%A %p") # e.g. Friday PM
    }

def get_curated_content(data):
    if not GEMINI_KEY:
        log("No Gemini Key provided.")
        return None

    log(f"Step 2: Requesting curation from Gemini for '{data['current_time']}'...")
    client = genai.Client(api_key=GEMINI_KEY)
    
    context = {
        "top_artist_recently": data['top_artist'],
        "time_context": data['current_time'],
        "recent_pool": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist'), "g": s.get('genre')} for s in data['recent_pool'][:150]],
        "library_samples": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist'), "g": s.get('genre')} for s in data['discovery'][:100]]
    }

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"Data Context: {json.dumps(context)}",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        log(f"Gemini curation failed: {e}")
        return None

def update_playlist(target_type, display_name, song_ids):
    if not song_ids: return
    
    # Load state to find the daylist ID
    state = load_playlist_map()
    all_playlists = call_subsonic("getPlaylists").get("playlists", {}).get("playlist", [])
    if not isinstance(all_playlists, list): all_playlists = [all_playlists] if all_playlists else []
    
    target_id = None
    
    if target_type == "daily":
        target_id = next((p['id'] for p in all_playlists if p.get('name') == "Daily Mix"), None)
        final_name = "Daily Mix"
    else:
        # Check if we already have a daylist ID tracked
        target_id = state.get("daylist_id")
        # Safety check: does it still exist in Navidrome?
        if target_id and not any(p['id'] == target_id for p in all_playlists):
            target_id = None
        final_name = display_name

    params = get_auth_params()
    
    if target_id:
        # UPDATE EXISTING (Subsonic's createPlaylist with a playlistId renames and replaces)
        params.update({"playlistId": target_id, "name": final_name})
        log(f"Updating {target_type} (ID: {target_id}) to '{final_name}'")
    else:
        # CREATE NEW
        params.update({"name": final_name})
        log(f"Creating new {target_type} named '{final_name}'")
    
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in song_ids[:50]])
    
    # Execute the update/create
    requests.get(f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}")
    
    # If we created a new daylist, find its ID and save it so we can rename it later
    if target_type == "daylist" and not target_id:
        time.sleep(1) # Give server a moment to index
        refreshed_lists = call_subsonic("getPlaylists").get("playlists", {}).get("playlist", [])
        if not isinstance(refreshed_lists, list): refreshed_lists = [refreshed_lists] if refreshed_lists else []
        new_id = next((p['id'] for p in refreshed_lists if p.get('name') == final_name), None)
        if new_id:
            state["daylist_id"] = new_id
            save_playlist_map(state)

def run_cycle():
    log("--- Starting Curation Cycle ---")
    data = fetch_music_data()
    result = get_curated_content(data)
    
    if result:
        # Handle Daily Mix
        if "daily_mix" in result:
            update_playlist("daily", "Daily Mix", result["daily_mix"])
            
        # Handle daylist (lowercase name)
        if "daylist_name" in result and "daylist_ids" in result:
            update_playlist("daylist", result["daylist_name"], result["daylist_ids"])
                
    log("--- Curation Cycle Complete ---")

if __name__ == "__main__":
    log("Spotidrome Personalized Curation Service Started")
    run_cycle()
    
    # Run every 6 hours (Morning, Afternoon, Evening, Night)
    schedule.every(6).hours.do(run_cycle)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

