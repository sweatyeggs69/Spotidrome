Spotidrome

Spotidrome is an AI-powered playlist generator for Navidrome (and other Subsonic-compatible servers). It analyzes your listening history, favorites, and library to curate a "Daily Mix" with a unique title and description, just like Spotify.

Prerequisites

A Navidrome server instance.

A Google Gemini API Key (Get one for free at Google AI Studio).

Setup

Create a folder named spotidrome.

Save the main.py, Dockerfile, requirements.txt, and docker-compose.yml into that folder.

Edit the docker-compose.yml with your server details.

Run docker-compose up --build.