FROM python:3.11-slim

LABEL name="Spotidrome"
LABEL description="AI-powered Playlist Generator for Navidrome"

# Prevents Python from writing .pyc files and ensures logs appear instantly
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY main.py .

# Security: Run as a non-privileged user
RUN useradd -m spotidromeuser
USER spotidromeuser

CMD ["python", "main.py"]