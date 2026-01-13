<img width="64" height="64" alt="spotidrome logo" src="https://github.com/user-attachments/assets/755edb71-a0bc-4c75-ba14-709653f71569" />

# Spotidrome 

Spotidrome is a playlist generator for Navidrome (and other Subsonic-compatible servers), with optional Google Gemini integration. It analyzes your listening history, favorites and library to curate a "Daily Mix" playlist.

> [!NOTE]
> This was created using Gemini; solely because I am not a developer, but I have looked over the changes made each time to ensure accuracy.

### Prerequisites

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
         - GEMINI_MODEL=gemini-2.5-flash-lite #optional
       restart: unless-stopped
