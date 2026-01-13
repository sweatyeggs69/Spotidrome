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
OUTPUT: {"ids": ["id1", "id2", ...]}
"""

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
        print(f"[{datetime.now()}] API Error ({endpoint}): {e}")
        return {}

def fetch_music_data():
    """Uses only stable endpoints to build a portrait of the user's taste."""
    print(f"[{datetime.now()}] Analyzing library activity...")
    
    artist_counts = {}
    recent_pool = []
    seen_ids = set()

    # Get 'Recent' items (Albums recently played or added)
    recent_data = call_subsonic("getAlbumList", {"type": "recent", "size": 40})
    albums = recent_data.get("albumList", {}).get("album", [])
    if not isinstance(albums, list): albums = [albums] if albums else []

    for alb in albums:
        artist = alb.get('artist', 'Unknown')
        # Weight recent activity higher
        artist_counts[artist] = artist_counts.get(artist, 0) + 2
        
        album_details = call_subsonic("getAlbum", {"id": alb['id']})
        tracks = album_details.get("album", {}).get("song", [])
        if not isinstance(tracks, list): tracks = [tracks] if tracks else []
        
        for t in tracks:
            if t['id'] not in seen_ids:
                recent_pool.append(t)
                seen_ids.add(t['id'])

    # Get 'Frequent' items (High rotation over time)
    frequent_data = call_subsonic("getAlbumList", {"type": "frequent", "size": 40})
    f_albums = frequent_data.get("albumList", {}).get("album", [])
    if not isinstance(f_albums, list): f_albums = [f_albums] if f_albums else []

    for alb in f_albums:
        artist = alb.get('artist', 'Unknown')
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        
        album_details = call_subsonic("getAlbum", {"id": alb['id']})
        tracks = album_details.get("album", {}).get("song", [])
        if tracks:
            sample = random.sample(tracks, min(len(tracks), 3))
            for t in sample:
                if t['id'] not in seen_ids:
                    recent_pool.append(t)
                    seen_ids.add(t['id'])

    top_artist = max(artist_counts, key=artist_counts.get) if artist_counts else "Various Artists"
    
    # Discovery Pool
    discovery_data = call_subsonic("getRandomSongs", {"size": 100})
    discovery = discovery_data.get("randomSongs", {}).get("song", [])

    return {
        "top_artist": top_artist,
        "recent_pool": recent_pool,
        "discovery": discovery
    }

def get_mix(data):
    # LOCAL FALLBACK
    if not GEMINI_KEY:
        print(f"[{datetime.now()}] Using local algorithmic shuffle...")
        pool = data['recent_pool']
        random.shuffle(pool)
        final_ids = [s['id'] for s in pool[:42]]
        discovery_ids = [s['id'] for s in data['discovery']]
        final_ids.extend(random.sample(discovery_ids, min(len(discovery_ids), 8)))
        return {"ids": final_ids}

    # AI CURATION WITH EXPONENTIAL BACKOFF
    print(f"[{datetime.now()}] Asking Gemini to curate the vibe...")
    client = genai.Client(api_key=GEMINI_KEY)
    context = {
        "top_artist_recently": data['top_artist'],
        "recent_pool": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['recent_pool'][:150]],
        "library_samples": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['discovery'][:50]]
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
            return json.loads(response.text)
        except Exception as e:
            if i < retries - 1:
                wait_time = (2 ** i)
                time.sleep(wait_time)
                continue
            print(f"[{datetime.now()}] Gemini Error after retries, falling back to local: {e}")
            return get_mix({**data, "GEMINI_KEY": None})

def update_playlist(song_ids):
    playlist_name = "Daily Mix"
    # 1. Check if playlist exists
    lists_resp = call_subsonic("getPlaylists")
    lists = lists_resp.get("playlists", {}).get("playlist", [])
    if not isinstance(lists, list): lists = [lists] if lists else []
    
    target_id = next((p['id'] for p in lists if p.get('name') == playlist_name), None)
    
    # 2. Update or Create
    if target_id:
        print(f"[{datetime.now()}] Refreshing existing '{playlist_name}'...")
        # Note: Navidrome's createPlaylist overwrites if songIds are provided with a playlistId
        params = get_auth_params()
        params.update({"playlistId": target_id})
    else:
        print(f"[{datetime.now()}] Creating new '{playlist_name}'...")
        params = get_auth_params()
        params.update({"name": playlist_name})
    
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in song_ids[:50]])
    
    # Final API call to save the list
    requests.get(f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}")
    print(f"[{datetime.now()}] Success. Daily Mix updated.")

def job():
    print(f"[{datetime.now()}] --- Starting Cycle ---")
    data = fetch_music_data()
    mix = get_mix(data)
    if mix and "ids" in mix:
        update_playlist(mix['ids'])

if __name__ == "__main__":
    print(f"[{datetime.now()}] Spotidrome Service Started.")
    # Run once on startup
    job()
    # Then schedule for midnight
    schedule.every().day.at("00:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)

