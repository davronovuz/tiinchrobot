FROM python:3.11-slim

# ffmpeg — video merge, aria2 — parallel yuklash, nodejs — yt-dlp JS interpreter
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Bog'liqliklar alohida layer — kod o'zgarganda qayta o'rnatilmaydi
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp tez-tez yangilanadi (YouTube o'zgarishlari uchun) — har build'da eng so'nggisi.
# Bu layer eng oxirida bo'lsa cache buzilmaydi, lekin nightly yangilik kerak —
# shuning uchun CACHE_BUST argumenti bilan boshqariladi.
ARG YTDLP_REFRESH=1
RUN pip install --no-cache-dir --upgrade yt-dlp

COPY . .

# Vaqtinchalik fayllar uchun papkalar
RUN mkdir -p /tmp/tiinchbot_downloads /shared_media

# Python bufersiz log (docker logs real vaqtda ko'rinadi)
ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]
