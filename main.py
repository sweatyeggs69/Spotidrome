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
You are Spotidrome, a professional music curator. You generate JSON playlists.

TASKS:
1. Daily Mix: A balanced mix of recent favorites and discovery.
2. Genre Mixes: Focused playlists based on specific genres.

CRITICAL RULES:
- PLAYLIST NAMING: For genre mixes, use ONLY an evocative, vibey name (e.g., "Neon Deserts", "Steel and Soul", "Midnight Rhythms"). Do NOT include the genre name itself in the title.
- SONIC INTEGRITY: For genre-focused playlists, maintain the core energy. While you can include sub-genres or closely related styles to ensure a full list, avoid jarring transitions (e.g., no acoustic folk in a high-energy electronic mix).
- DIVERSITY: Do not repeat the same artist more than 3 times in one playlist.
- OUTPUT FORMAT: You must return a JSON object with keys for each requested playlist.
- EXACTNESS: Each playlist MUST have exactly 50 song IDs.

OUTPUT SCHEMA:
{
  "daily_mix": ["id1", "id2", ...],
  "genre_mixes": [
    {"name": "Vibey Name Only", "ids": ["id1", ...]},
    {"name": "Vibey Name Only", "ids": ["id1", ...]},
    {"name": "Vibey Name Only", "ids": ["id1", ...]}
  ]
}
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
    log("Step 1: Analyzing library activity and identifying top genres...")
    
    genre_counts = {}
    artist_counts = {}
    recent_pool = []
    seen_ids = set()

    recent_data = call_subsonic("getAlbumList", {"type": "recent", "size": 80})
    albums = recent_data.get("albumList", {}).get("album", [])
    if not isinstance(albums, list): albums = [albums] if albums else []

    for alb in albums:
        artist = alb.get('artist', 'Unknown')
        genre = alb.get('genre', 'Unknown')
        
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
        if genre != "Unknown":
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
        
        album_details = call_subsonic("getAlbum", {"id": alb['id']})
        tracks = album_details.get("album", {}).get("song", [])
        if not isinstance(tracks, list): tracks = [tracks] if tracks else []
        for t in tracks:
            if t['id'] not in seen_ids:
                recent_pool.append(t)
                seen_ids.add(t['id'])

    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
    top_3_genres = [g[0] for g in sorted_genres[:3]]
    top_artist = max(artist_counts, key=artist_counts.get) if artist_counts else "Various"
    
    discovery_data = call_subsonic("getRandomSongs", {"size": 400})
    discovery = discovery_data.get("randomSongs", {}).get("song", [])

    log(f"Library Analysis: Top Artist: {top_artist} | Top Genres: {', '.join(top_3_genres)}")
    
    return {
        "top_artist": top_artist,
        "top_genres": top_3_genres,
        "recent_pool": recent_pool,
        "discovery": discovery
    }

def get_curated_content(data, include_weekly=False):
    if not GEMINI_KEY:
        log("No Gemini Key. Curation aborted.")
        return None

    log(f"Step 2: Requesting curation from Gemini...")
    client = genai.Client(api_key=GEMINI_KEY)
    
    genre_samples = {}
    all_available_tracks = data['recent_pool'] + data['discovery']
    
    for genre in data['top_genres']:
        # Fuzzy matching: include sub-genres (e.g., "Metalcore" matches "Metal")
        # Also includes a random fallback subset to ensure Gemini has enough tracks to hit 50
        genre_base = genre.lower()
        safe_list = [
            {"id": s['id'], "t": s.get('title'), "a": s.get('artist'), "g": s.get('genre')} 
            for s in all_available_tracks 
            if genre_base in s.get('genre', '').lower() or s.get('genre', '').lower() in genre_base
        ]
        
        # If the filtered list is too small (under 60), supplement it with general discovery
        if len(safe_list) < 60:
            supplement = random.sample(all_available_tracks, min(len(all_available_tracks), 40))
            safe_list.extend([{"id": s['id'], "t": s.get('title'), "a": s.get('artist'), "g": s.get('genre')} for s in supplement])
            
        random.shuffle(safe_list)
        genre_samples[genre] = safe_list[:120] # Increased sample size for Gemini

    context = {
        "top_artist_recently": data['top_artist'],
        "top_genres_this_week": data['top_genres'],
        "recent_pool": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['recent_pool'][:100]],
        "genre_vibe_samples": genre_samples,
        "mode": "full_update" if include_weekly else "daily_only"
    }

    prompt = "Generate the Daily Mix."
    if include_weekly:
        prompt += f" ALSO generate 3 genre-focused playlists for these vibes: {', '.join(data['top_genres'])}. You have 120 tracks per vibe; select the best 50 that flow together. Sub-genres are welcome to fill the list."

    retries = 3
    for i in range(retries):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=f"{prompt}\nData: {json.dumps(context)}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            if i < retries - 1:
                log(f"Gemini error, retrying... ({i+1})")
                time.sleep(2)
                continue
            log(f"Gemini failed curation after retries: {e}")
            return None

def update_playlist(name, song_ids):
    if not song_ids:
        return
    
    # Final shuffle to ensure non-deterministic order in Navidrome
    random.shuffle(song_ids)
        
    lists = call_subsonic("getPlaylists").get("playlists", {}).get("playlist", [])
    if not isinstance(lists, list): lists = [lists] if lists else []
    
    target_id = next((p['id'] for p in lists if p.get('name') == name), None)
    params = get_auth_params()
    
    if target_id:
        params.update({"playlistId": target_id})
    else:
        params.update({"name": name})
    
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in song_ids[:50]])
    
    requests.get(f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}")
    log(f"Playlist '{name}' updated with {len(song_ids[:50])} tracks in random order.")

def run_update(full_weekly=False):
    log(f"--- Starting {'Weekly' if full_weekly else 'Daily'} Update Cycle ---")
    data = fetch_music_data()
    result = get_curated_content(data, include_weekly=full_weekly)
    
    if result:
        if "daily_mix" in result:
            update_playlist("Daily Mix", result["daily_mix"])
        
        if full_weekly and "genre_mixes" in result:
            for g_mix in result["genre_mixes"]:
                update_playlist(g_mix["name"], g_mix["ids"])
                
    log("--- Update Cycle Complete ---")

if __name__ == "__main__":
    log("Spotidrome Service Initialized")
    run_update(full_weekly=True)
    schedule.every().day.at("00:00").do(run_update, full_weekly=False)
    schedule.every().monday.at("01:00").do(run_update, full_weekly=True)
    
    while True:
        schedule.run_pending()
        time.sleep(60)
