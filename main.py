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

# Baked-in System Instructions
SYSTEM_INSTRUCTION = """
You are Spotidrome, an expert music curator. Your goal is to generate a 'Daily Mix' JSON for the user.

Logic:
1. Review the 'recent_favorites' (frequently played) and 'starred_gems' (explicitly loved).
2. Select 40-45 song IDs from these two lists to form the core of the mix. 
3. IMPORTANT: Shuffle these IDs so the playlist doesn't just group songs by the same artist or album together.
4. Select 5-10 songs from 'library_samples' that complement the vibe of the favorites to act as "discovery" tracks.
5. Total song count MUST NOT exceed 50.

OUTPUT FORMAT (Strict JSON only):
{
  "ids": ["id1", "id2", "id3", ...]
}
"""

def check_env():
    """Validates required environment variables."""
    missing = []
    if not URL: missing.append("NAVIDROME_URL")
    if not USER: missing.append("NAVIDROME_USER")
    if not PASS: missing.append("NAVIDROME_PASS")
    
    if missing:
        print(f"[{datetime.now()}] FATAL ERROR: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)
    
    if not GEMINI_KEY:
        print(f"[{datetime.now()}] WARNING: No Gemini API Key found. Operating in Algorithmic Fallback mode.")
    else:
        print(f"[{datetime.now()}] Configuration active using AI model: {GEMINI_MODEL}")

def get_auth_params():
    salt = "".join([random.choice("0123456789abcdef") for _ in range(10)])
    token = hashlib.md5((PASS + salt).encode()).hexdigest()
    return {"u": USER, "t": token, "s": salt, "v": "1.16.1", "c": "Spotidrome", "f": "json"}

def call_subsonic(endpoint, extra_params={}):
    params = get_auth_params()
    params.update(extra_params)
    try:
        response = requests.get(f"{URL}/rest/{endpoint}.view", params=params, timeout=20)
        response.raise_for_status()
        data = response.json().get("subsonic-response", {})
        if data.get("status") == "failed":
            error = data.get("error", {})
            print(f"Subsonic API Error in {endpoint}: {error.get('message')}")
            return {}
        return data
    except Exception as e:
        print(f"Connection Error ({endpoint}): {e}")
        return {}

def fetch_music_data():
    print(f"[{datetime.now()}] Fetching music data from Navidrome...")
    
    frequent_albums_data = call_subsonic("getAlbumList", {"type": "frequent", "size": 80})
    albums = frequent_albums_data.get("albumList", {}).get("album", [])
    
    recent_favorites = []
    for album in albums:
        album_data = call_subsonic("getAlbum", {"id": album['id']})
        album_tracks = album_data.get("album", {}).get("song", [])
        if album_tracks:
            sample_count = random.randint(2, 5)
            recent_favorites.extend(random.sample(album_tracks, min(len(album_tracks), sample_count)))
    
    starred_data = call_subsonic("getStarred2")
    starred = starred_data.get("starred2", {}).get("song", [])
    if not isinstance(starred, list): starred = [starred] if starred else []

    discovery_data = call_subsonic("getRandomSongs", {"size": 200})
    discovery = discovery_data.get("randomSongs", {}).get("song", [])
    if not isinstance(discovery, list): discovery = [discovery] if discovery else []

    return {
        "history": recent_favorites,
        "starred": starred,
        "discovery": discovery
    }

def algorithmic_fallback(data):
    """Generates a 50-track mix using local shuffling logic."""
    print(f"[{datetime.now()}] Running local Algorithmic Curation...")
    
    all_favorites = data['history'] + data['starred']
    discovery_pool = data['discovery']
    
    random.shuffle(all_favorites)
    random.shuffle(discovery_pool)
    
    # 43 Favorites + 7 Discoveries = 50 Total
    fav_selection = all_favorites[:43]
    disc_selection = discovery_pool[:7]
    
    final_pool = fav_selection + disc_selection
    random.shuffle(final_pool)
    
    return {
        "ids": [s['id'] for s in final_pool]
    }

def get_ai_curation(data):
    """Attempts Gemini curation with a fallback to algorithmic logic on failure."""
    if not GEMINI_KEY:
        return algorithmic_fallback(data)

    client = genai.Client(api_key=GEMINI_KEY)
    
    seen_ids = set()
    def unique_tracks(track_list, limit):
        result = []
        if not track_list: return result
        shuffled_list = list(track_list)
        random.shuffle(shuffled_list)
        for s in shuffled_list:
            if not s or 'id' not in s: continue
            if s['id'] not in seen_ids:
                result.append({"id": s['id'], "t": s.get('title', 'Unknown'), "a": s.get('artist', 'Unknown')})
                seen_ids.add(s['id'])
            if len(result) >= limit: break
        return result

    context = {
        "recent_favorites": unique_tracks(data['history'], 150),
        "starred_gems": unique_tracks(data['starred'], 80),
        "library_samples": unique_tracks(data['discovery'], 100)
    }

    for i in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=f"User Library Context: {json.dumps(context)}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"[{datetime.now()}] Gemini Attempt {i+1} failed: {e}")
            time.sleep(2 ** i)

    print(f"[{datetime.now()}] AI curation failed. Falling back to local algorithm.")
    return algorithmic_fallback(data)

def update_daily_mix_playlist(song_ids):
    """Updates Navidrome playlist by overwriting its contents."""
    playlist_name = "Daily Mix"
    final_song_list = song_ids[:50]
    
    # 1. Check if it exists
    playlists_data = call_subsonic("getPlaylists")
    playlists = playlists_data.get("playlists", {}).get("playlist", [])
    if not isinstance(playlists, list): playlists = [playlists] if playlists else []
    
    target_id = next((p['id'] for p in playlists if p.get('name') == playlist_name), None)
    
    params = get_auth_params()
    
    if target_id:
        # Use existing playlistId to overwrite contents
        print(f"[{datetime.now()}] Replacing tracks in existing '{playlist_name}' (ID: {target_id})...")
        params.update({"playlistId": target_id})
    else:
        # Create new if it doesn't exist
        print(f"[{datetime.now()}] '{playlist_name}' not found. Creating new...")
        params.update({"name": playlist_name})
    
    comment_text = "AI Curated Mix" if GEMINI_KEY else "Algorithmically Curated Mix"
    params.update({"comment": comment_text})
    
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in final_song_list])
    
    # Calling createPlaylist with playlistId overwrites the list
    update_url = f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}"
    requests.get(update_url)

    print(f"[{datetime.now()}] Successfully updated '{playlist_name}'.")

def job():
    print(f"[{datetime.now()}] --- Starting Refresh Job ---")
    data = fetch_music_data()
    if not data['history'] and not data['starred']:
        print("Error: No music data found.")
        return

    curation = get_ai_curation(data)
    if curation and "ids" in curation:
        update_daily_mix_playlist(curation['ids'])
    else:
        print(f"[{datetime.now()}] Refresh failed.")

def main():
    print(f"[{datetime.now()}] Spotidrome initializing...")
    check_env()
    job()
    schedule.every().day.at("00:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
