<img height="80" alt="spotidrome-logo" src="https://github.com/user-attachments/assets/c03dc645-4917-454a-9dda-1877a23952ef" />

# Spotidrome 

Spotidrome is a playlist generator for Navidrome (and other Subsonic-compatible servers), with optional Google Gemini integration. It analyzes your listening history, favorites and library to curate a "Daily Mix" playlist.

> [!NOTE]
> This was created using Gemini; solely because I am not a developer, but I have looked over the changes made each time to ensure accuracy.

## Prerequisites

- A Navidrome server instance.
- A Google Gemini API Key (Get one for free at Google AI Studio).

## Docker Setup
```yaml
services:
     Spotidrome:
       image: sweatyeggs69/spotidrome:latest
       container_name: spotidrome
       environment:
         - NAVIDROME_URL=http://navidrome:4533
         - NAVIDROME_USER=your_username
         - NAVIDROME_PASS=your_password
       # - GEMINI_API_KEY=your_gemini_api_key #optional
       # - GEMINI_MODEL=gemini-2.5-flash-lite #optional
       restart: unless-stopped
```

## How It Works
### With AI Integration (Gemini Active)
#### When a Gemini API key is provided, the script acts as an intelligent music curator:
- Contextual Analysis: The script sends a "snapshot" of your library to Gemini, including your most frequent albums, starred tracks, and a random sample of your library.
- Intelligent Selection: Gemini analyzes the genres, artists, and "vibe" of your favorites. It then picks a cohesive set of tracks that match your taste while deliberately selecting a few "discovery" songs from the library samples.
- Human-like Shuffling: The AI is instructed to shuffle the results so the playlist feels like a professionally curated radio mix rather than just a list of songs sorted by artist or date.

### Without AI Integration (Algorithmic Fallback)
#### If the API key is missing or the AI service is unreachable, the script switches to a local, rules-based logic:
- Weighted Randomization: It combines your recently played tracks and starred tracks into a "Favorites Pool."
- Fixed Ratio: It strictly follows a mathematical ratio: 43 songs are pulled randomly from your favorites, and 7 songs are pulled from the rest of your library for discovery.
- Basic Shuffle: It performs a standard random shuffle on the final 50 tracks to ensure variety.
- Reliability: This mode requires zero external internet access (other than your Navidrome server), ensuring your "Daily Mix" is updated even if the AI service is down.

### The "Update" Process (Same for both)
#### Regardless of whether AI is used, the final step is the same:
- Playlist Discovery: The script looks for a playlist named "Daily Mix" in your Navidrome account.
- Atomic Update: It uses the playlistId to overwrite the contents. This means you don't have to "re-follow" the playlist on your phone or computer; the songs simply change inside the existing playlist container every night at midnight.
