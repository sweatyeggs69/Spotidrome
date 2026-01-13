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

# --- System Instruction ---
# Modify this string directly to change the behavior of the AI curator.
SYSTEM_INSTRUCTION = """
You are Spotidrome, an expert music curator. Your goal is to generate a 'Daily Mix' JSON for the user.

Logic:
1. Review the 'recent_favorites' (songs from your most frequently played albums lately).
2. You will be told the 'top_artist_recently'. Use this as the primary anchor for the "vibe" of today's mix.
3. Select 40-45 song IDs from the 'recent_favorites' list to form the core of the mix. 
4. IMPORTANT: Shuffle these IDs so the playlist isn't grouped by artist or album.
5. Select 5-10 songs from 'library_samples' for discovery that fit the established vibe of your recent listening.
6. Total song count MUST NOT exceed 50.

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
        print(f"[{datetime.now()}] WARNING: No Gemini API Key. Using Algorithmic Fallback.")
    else:
        print(f"[{datetime.now()}] Configured with model: {GEMINI_MODEL}")
        print(f"[{datetime.now()}] Using internal System Instruction for curation logic.")

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
        return data
    except Exception as e:
        print(f"Connection Error ({endpoint}): {e}")
        return {}

def fetch_music_data():
    print(f"[{datetime.now()}] Fetching music data from Navidrome...")
    
    # Use 'frequent' albums to find Top Artist and Recent Favorites
    frequent_albums_data = call_subsonic("getAlbumList", {"type": "frequent", "size": 60})
    albums = frequent_albums_data.get("albumList", {}).get("album", [])
    if not isinstance(albums, list): albums = [albums] if albums else []
    
    artist_counts = {}
    recent_favorites = []
    
    for album in albums:
        a_name = album.get('artist')
        if a_name: artist_counts[a_name] = artist_counts.get(a_name, 0) + 1
        
        album_data = call_subsonic("getAlbum", {"id": album['id']})
        album_tracks = album_data.get("album", {}).get("song", [])
        if album_tracks:
            # Take a healthy sample from recently frequent albums
            sample_count = random.randint(3, 6)
            recent_favorites.extend(random.sample(album_tracks, min(len(album_tracks), sample_count)))
    
    top_artist = max(artist_counts, key=artist_counts.get) if artist_counts else "Unknown"
    print(f"[{datetime.now()}] Top artist from recent history: {top_artist}")

    # Starred data removed to focus on recent frequency

    discovery_data = call_subsonic("getRandomSongs", {"size": 200})
    discovery = discovery_data.get("randomSongs", {}).get("song", [])
    if not isinstance(discovery, list): discovery = [discovery] if discovery else []

    return {
        "top_artist": top_artist,
        "history": recent_favorites,
        "discovery": discovery
    }

def get_ai_curation(data):
    """Attempts Gemini curation using the hardcoded SYSTEM_INSTRUCTION."""
    if not GEMINI_KEY:
        # Simple shuffle fallback
        all_ids = [s['id'] for s in (data['history'] + data['discovery'][:10])]
        random.shuffle(all_ids)
        return {"ids": all_ids[:50]}

    client = genai.Client(api_key=GEMINI_KEY)
    
    context = {
        "top_artist_recently": data['top_artist'],
        "recent_favorites": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['history'][:150]],
        "library_samples": [{"id": s['id'], "t": s.get('title'), "a": s.get('artist')} for s in data['discovery'][:100]]
    }

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
        print(f"[{datetime.now()}] AI curation error: {e}")
        return None

def update_daily_mix_playlist(song_ids):
    playlist_name = "Daily Mix"
    playlists_data = call_subsonic("getPlaylists")
    playlists = playlists_data.get("playlists", {}).get("playlist", [])
    if not isinstance(playlists, list): playlists = [playlists] if playlists else []
    
    target_id = next((p['id'] for p in playlists if p.get('name') == playlist_name), None)
    params = get_auth_params()
    
    if target_id:
        print(f"[{datetime.now()}] Updating tracks in existing '{playlist_name}' (ID: {target_id})...")
        params.update({"playlistId": target_id})
    else:
        print(f"[{datetime.now()}] '{playlist_name}' not found. Creating new...")
        params.update({"name": playlist_name})
    
    params.update({"comment": f"Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}"})
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in song_ids[:50]])
    
    requests.get(f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}")
    print(f"[{datetime.now()}] Successfully updated '{playlist_name}'.")

def job():
    print(f"[{datetime.now()}] --- Starting Refresh Job ---")
    data = fetch_music_data()
    curation = get_ai_curation(data)
    if curation and "ids" in curation:
        update_daily_mix_playlist(curation['ids'])
    else:
        print(f"[{datetime.now()}] Refresh job completed with no updates.")

def main():
    check_env()
    job()
    schedule.every().day.at("00:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
