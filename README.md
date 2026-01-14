<img height="80" alt="spotidrome-logo" src="https://github.com/user-attachments/assets/c03dc645-4917-454a-9dda-1877a23952ef" />

# Spotidrome 

Spotidrome is a playlist generator for Navidrome (and other Subsonic-compatible servers), with optional Google Gemini integration. It analyzes your listening history, favorites and library to curate a "Daily Mix" playlist that is automatically updated every day at midnight.

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
         - GEMINI_API_KEY=your_gemini_api_key #optional
         - GEMINI_MODEL=desired-gemini-model #optional, default behavior uses the lite-preview model
       restart: unless-stopped
```

## How It Works
### With Gemini Active
#### When a Gemini API key is provided, the script acts as an intelligent music curator:
- Dynamic Context: The script analyzes your frequent albums (the 60 albums you've played most often lately) to identify your current musical "vibe."
- Top Artist Anchor: It calculates your most-played artist from recent history and uses them as a primary anchor for the day's curation.
- Intelligent Selection: Gemini reviews a pool of ~150 songs from your recent favorites and ~100 random samples from your library to build a cohesive 50-track mix.
- Human-like Shuffling: The AI is instructed to shuffle tracks to ensure the mix feels like a hand-crafted radio station rather than a sorted list.

### Without Gemini (Algorithmic Fallback)
#### If the API key is not provided or the AI service is unreachable, the script switches to local logic:
- Weighted Selection: It pulls tracks randomly from your recently frequent albums.
- Discovery Blend: It fills the remaining slots with random tracks from your library to maintain a roughly 80/20 split between favorites and discovery.
- Basic Shuffle: It performs a standard random shuffle on the final 50 tracks.

## The "Update" Process
- Playlist Discovery: The script looks for a playlist named "Daily Mix" in your Navidrome account.
- Atomic Update: It overwrites the contents of the existing playlist daily. This means you don't have to "re-follow" the playlist; the music just refreshes every night at midnight.
