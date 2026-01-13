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
You are Spotidrome, an expert music curator. 
Your goal is to generate a 'Daily Mix' JSON that is SONICALLY CONSISTENT.

CRITICAL LOGIC:
1. You will be provided with a 'Seed Artist'.
2. Review the provided 'recent_favorites', 'starred_gems', and 'library_samples'.
3. Select 50 songs that match the MOOD, GENRE, or VIBE of the 'Seed Artist'.
4. DO NOT mix jarringly different genres (e.g., No Heavy Metal if the seed is Jazz).
5. Ensure the mix feels like a cohesive radio station.

OUTPUT FORMAT (Strict JSON only):
{"ids": ["id1", "id2", ...]}
"""

def get_auth_params():
    salt = "".join([random.choice("0123456789abcdef") for _ in range(10)])
    token = hashlib.md5((PASS + salt).encode()).hexdigest()
    return {"u": USER, "t": token, "s": salt, "v": "1.16.1", "c": "Spotidrome", "f": "json"}

def call_subsonic(endpoint, extra_params={}):
    params = get_auth_params()
    params.update(extra_params)
    try:
        response = requests.get(f"{URL}/rest/{endpoint}.view", params=params, timeout=20)
        data = response.json().get("subsonic-response", {})
        return data
    except: return {}

def fetch_music_data():
    print(f"[{datetime.now()}] Fetching music data and identifying a daily vibe...")
    
    # Get favorites to pick a seed
    starred = call_subsonic("getStarred2").get("starred2", {}).get("song", [])
    if not isinstance(starred, list): starred = [starred] if starred else []
    
    frequent = call_subsonic("getAlbumList", {"type": "frequent", "size": 40}).get("albumList", {}).get("album", [])
    
    # Pick a Seed Artist from stars or frequent
    seed_pool = starred + frequent
    seed_item = random.choice(seed_pool) if seed_pool else None
    seed_artist = seed_item.get('artist', 'Various') if seed_item else "Modern"
    
    print(f"[{datetime.now()}] Today's Seed Vibe: {seed_artist}")

    # Gather broader samples for the AI to pick from
    discovery = call_subsonic("getRandomSongs", {"size": 300}).get("randomSongs", {}).get("song", [])
    
    return {
        "seed": seed_artist,
        "starred": starred,
        "discovery": discovery
    }

def get_ai_curation(data):
    if not GEMINI_KEY:
        # Simple random fallback if no AI
        combined = data['starred'] + data['discovery']
        random.shuffle(combined)
        return {"ids": [s['id'] for s in combined[:50]]}

    client = genai.Client(api_key=GEMINI_KEY)
    
    # Simplify context to save tokens and focus the AI
    context = {
        "seed_vibe": data['seed'],
        "pool": [{"id": s['id'], "title": s.get('title'), "artist": s.get('artist'), "genre": s.get('genre')} 
                 for s in (data['starred'][:100] + data['discovery'][:150])]
    }

    for i in range(3):
        try:
            prompt = f"Create a 50-song playlist based on the seed: {data['seed']}. Data: {json.dumps(context)}"
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"[{datetime.now()}] Gemini Attempt {i+1} failed: {e}")
            time.sleep(2)
    return None

def update_daily_mix_playlist(song_ids):
    playlist_name = "Daily Mix"
    final_list = song_ids[:50]
    
    playlists = call_subsonic("getPlaylists").get("playlists", {}).get("playlist", [])
    if not isinstance(playlists, list): playlists = [playlists] if playlists else []
    target_id = next((p['id'] for p in playlists if p.get('name') == playlist_name), None)
    
    params = get_auth_params()
    if target_id:
        params.update({"playlistId": target_id})
    else:
        params.update({"name": playlist_name})
    
    params.update({"comment": f"AI Mix seeded by a random favorite. Updated {datetime.now().strftime('%Y-%m-%d')}"})
    
    auth_str = "&".join([f"{k}={v}" for k, v in params.items()])
    song_str = "&".join([f"songId={sid}" for sid in final_list])
    requests.get(f"{URL}/rest/createPlaylist.view?{auth_str}&{song_str}")
    print(f"[{datetime.now()}] Successfully updated '{playlist_name}'.")

def job():
    print(f"[{datetime.now()}] --- Starting Genre-Consistent Refresh ---")
    data = fetch_music_data()
    curation = get_ai_curation(data)
    if curation and "ids" in curation:
        update_daily_mix_playlist(curation['ids'])

def main():
    job()
    schedule.every().day.at("00:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    main()
