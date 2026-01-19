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

SYSTEM_INSTRUCTION = """
You are Spotidrome, a professional music curator. Generate a 'Daily Mix' JSON.
Logic: 
- Anchor the mix around the 'top_artist_recently'.
- Select ~40 tracks from 'recent_pool' (favoring variety).
- Select ~10 tracks from 'library_samples' for discovery.
- Total tracks must be exactly 50.
- Shuffle the list so it feels like a curated radio station.
- Ensure we are staying within similar genres to the anchor artist.
OUTPUT: {"ids": ["id1", "id2", ...]}
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

def fetch_music_data():
    log("Step 1: Scanning Navidrome for recent activity...")
    
    artist_counts = {}
    recent_pool = []
    seen_ids = set()

    # Recent Albums
    recent_data = call_subsonic("getAlbumList", {"type": "recent", "size": 40})
    albums = recent_data.get("albumList", {}).get("album", [])
    if not isinstance(albums, list): albums = [albums] if albums else []

    for alb in albums:
        artist = alb.get('artist', 'Unknown')
        artist_counts[artist] = artist_counts.get(artist, 0) + 2
        
        album_details = call_subsonic("getAlbum", {"id": alb['id']})
        tracks = album_details.get("album", {}).get("song", [])
        if not isinstance(tracks, list): tracks = [tracks] if tracks else []
        for t in tracks:
            if t['id'] not in seen_ids:
                recent_pool.append(t)
                seen_ids.add(t['id'])

    # Frequent Albums
    frequent_data = call_subsonic("getAlbumList", {"type": "frequent", "size": 20})
    f_albums = frequent_data.get("albumList", {}).get("album", [])
    if not isinstance(f_albums, list): f_albums = [f_albums] if f_albums else []

    for alb in f_albums:
        artist = alb.get('artist', 'Unknown')
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
    
    top_artist = max(artist_counts, key=artist_counts.get) if artist_counts else "Various"
    
    # Discovery Pool
    discovery_data = call_subsonic("getRandomSongs", {"size": 100})
    discovery = discovery_data.get("randomSongs", {}).get("song", [])

    log(f"Data Found: {len(recent_pool)} recent tracks. Anchor artist: '{top_artist}'.")
    return {
        "top_artist": top_artist,
        "recent_pool": recent_pool,
        "discovery": discovery
    }

def get_mix(data):
    if not GEMINI_KEY:
        log("No Gemini Key found. Performing basic algorithmic shuffle.")
        pool = data['recent_pool']
        random.shuffle(pool)
        final_ids = [s['id'] for s in pool[:42]]
        discovery_ids = [s['id'] for s in data['discovery']]
        final_ids.extend(random.sample(discovery_ids, min(len(discovery_ids), 8)))
        return {"ids": final_ids}

    log(f"Step 2: Requesting curation from Gemini ({GEMINI_MODEL})...")
    client = genai.Client(api_key=GEMINI_KEY)
    
    context = {
        "top_artist_recently": data['top_artist'],
        "recent_pool": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['recent_pool'][:120]],
        "library_samples": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['discovery'][:40]]
    }

    retries = 5
    for i in range(retries):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=f"Data: {json.dumps(context)}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
            result = json.loads(response.text)
            log(f"Gemini successfully curated a mix of {len(result.get('ids', []))} tracks.")
            return result
        except Exception as e:
            if i < retries - 1:
                log(f"Retrying Gemini request (Attempt {i+2}/{retries})...")
                time.sleep(2 ** i)
                continue
            log("Gemini failed after retries. Falling back to local shuffle.")
            return {"ids": [s['id'] for s in random.sample(data['recent_pool'], min(len(data['recent_pool']), 50))]}

def update_playlist(song_ids):
    log("Step 3: Syncing playlist with Navidrome...")
    playlist_name = "Daily Mix"
    
    lists = call_subsonic("getPlaylists").get("playlists", {}).get("playlist", [])
    if not isinstance(lists, list): lists = [lists] if lists else []
    
    target_id = next((p['id'] for p in lists if p.get('name') == playlist_name), None)
    params = get_auth_params()
    
    if target_id:
        params.update({"playlistId": target_id})
    else:
        params.update({"name": playlist_name})
    
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in song_ids[:50]])
    
    requests.get(f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}")
    log(f"Playlist '{playlist_name}' update complete.")

def job():
    log("--- Daily Mix Update Started ---")
    data = fetch_music_data()
    mix = get_mix(data)
    if mix and "ids" in mix:
        update_playlist(mix['ids'])
    log("--- Update Cycle Complete ---")

if __name__ == "__main__":
    log("Spotidrome Service Initialized")
    job()
    schedule.every().day.at("00:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)
