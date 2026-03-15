FROM python:3.11-slim

# ffmpeg kerak yt-dlp uchun (video merge)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Vaqtinchalik fayllar uchun papka
RUN mkdir -p /tmp/tiinchbot_downloads

CMD ["python", "app.py"]
