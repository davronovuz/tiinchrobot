# 🚀 TiinchRobot — Keyingi Daraja: Master Reja

> Maqsad: deyarli barcha platformalardan mukammal yuklash, kuchli musiqa qidiruv, maksimal tezlik.
> Tuzilgan sana: 2026-08-31

---

## Hozirgi holat (audit xulosasi)

**Kuchli tomonlar:** Cobalt → platforma API → yt-dlp+WARP fallback zanjiri ishlaydi; file_id cache (Redis+Postgres); bgutil PO token; Pyrogram katta fayllar uchun; Shazam.

**Zaif tomonlar (aniqlangan):**
1. `is_supported_url()` oq ro'yxat — yt-dlp 1800+ saytni qo'llasa ham, ro'yxatda yo'q platformalar rad etiladi
2. Musiqa qidiruv YouTube qismi yt-dlp `ytsearch` orqali — sekin (5–15s), timeout tez-tez
3. `aria2c` Dockerga o'rnatilgan, lekin yt-dlp da **umuman ishlatilmayapti** (kommentda bor, kodda yo'q)
4. Har so'rovda yangi `httpx.AsyncClient` — TLS handshake isrofi
5. Yuborish tezligi: >50MB da Pyrogram MTProto sekin; Bot API 50MB limit
6. `user_results`, `_yt_format_cache` xotirada — restart da yo'qoladi
7. Qidiruv natijalari cache'lanmaydi — bir xil so'rov har safar qayta qidiriladi
8. Twitter/X, TikTok photo-slide, Threads uchun maxsus tez yo'l yo'q
9. Deploy: server load ~32, har build 10+ daqiqa

---

## FAZA 1 — Yuklash tezligi (eng katta ta'sir, 1 kun)

| # | Ish | Fayl | Ta'sir |
|---|-----|------|--------|
| 1.1 | **Local Bot API server** (`aiogram/telegram-bot-api` container) — 50MB limit → 2GB, upload 5–10x tez, Pyrogram keraksiz bo'ladi | docker-compose.yml, loader.py | ⭐⭐⭐⭐⭐ |
| 1.2 | **aria2c external downloader** yt-dlp ga ulash (`-x16 -s16 -k1M`) — to'g'ridan-to'g'ri HTTP yuklashlar 3–5x tez | video_downloader.py, music_search.py | ⭐⭐⭐⭐ |
| 1.3 | **Global httpx.AsyncClient** (HTTP/2, connection pool) — har so'rovda TLS handshake yo'qoladi | video_downloader.py, music_search.py | ⭐⭐ |
| 1.4 | **uvloop** — asyncio 2–4x tez event loop | app.py, requirements.txt | ⭐⭐ |
| 1.5 | yt-dlp: `http_chunk_size: 10MB`, `buffersize`, `--no-part` | video_downloader.py | ⭐⭐ |
| 1.6 | Dedicated `ThreadPoolExecutor(max_workers=16)` — default executor tiqilib qolmasin | video_downloader.py | ⭐⭐ |
| 1.7 | Thumbnail yuklashni video yuklash bilan **parallel** qilish | echo.py | ⭐ |

## FAZA 2 — Platforma qamrovi: "deyarli hammasi" (1–2 kun)

| # | Ish | Tafsilot |
|---|-----|----------|
| 2.1 | **Universal fallback**: oq ro'yxatda bo'lmagan har qanday http URL uchun ham yt-dlp urinish (qora ro'yxat bilan: telegram, google docs, ...) — Twitch clips, Bilibili, VK, OK.ru, Rutube, Tumblr, Threads, 9GAG va 1800+ sayt avtomatik ishlaydi | video_downloader.py |
| 2.2 | **Twitter/X tez yo'l**: fxtwitter API (`api.fxtwitter.com`) — auth kerak emas, <1s, rasmlar+videolar+carousel | yangi `_download_twitter()` |
| 2.3 | **TikTok photo-slide** (rasm karusel) qo'llash — tikwm `images` maydoni hozir e'tibordan chetda | `_download_tiktok()` |
| 2.4 | **TikTok fallback zanjiri**: tikwm → tiklydown/douyin → yt-dlp (tikwm rate-limit bo'lsa) | video_downloader.py |
| 2.5 | **Instagram mustahkamlash**: v1 API → embed-page scraping fallback (sessionid o'lsa ham public postlar ishlaydi); sessionid o'lganda adminga avtomatik ogohlantirish | video_downloader.py |
| 2.6 | **Threads** qo'llash (threads.net → yt-dlp/API) | SUPPORTED_PLATFORMS |
| 2.7 | **SoundCloud + Spotify link** → musiqa pipeline'ga yo'naltirish (Spotify link → artist/title parse → Deezer/YT dan yuklash) | echo.py |
| 2.8 | Platformaga xos **aniq xato xabarlari** ("Instagram: post yopiq profil", "YouTube: 2 soatdan uzun", ...) | echo.py |

## FAZA 3 — Musiqa qidiruv: keyingi daraja (1 kun)

| # | Ish | Tafsilot |
|---|-----|----------|
| 3.1 | **ytmusicapi** — YouTube Music rasmiy ichki API: qidiruv <1s (hozir 5–15s), faqat qo'shiqlar (video-blog aralashmaydi), toza artist/title/album/cover metadata | requirements.txt, music_search.py |
| 3.2 | **Qidiruv natijalarini Redis cache** (query → natijalar, TTL 6 soat) — takroriy qidiruvlar 0s | music_search.py |
| 3.3 | **iTunes Search API** qo'shish (bepul, kalitsiz) — Deezer+iTunes+YTMusic uch manba, eng boy natija | music_search.py |
| 3.4 | **Inline mode** (`@tinchrobot qo'shiq nomi` istalgan chatda) — viral o'sish mexanizmi | yangi handler, BotFather sozlash |
| 3.5 | Natija ranking: aniq mos kelish > official audio > remix/cover; duration bo'yicha sog'lom filtr | music_search.py |
| 3.6 | `/top`, `/new` ni statik fayllardan **Deezer Charts API** ga o'tkazish — har doim yangi | music_search.py |
| 3.7 | Shazam natijasiga **"O'xshash qo'shiqlar"** tugmasi (Deezer related API) | music_search.py |
| 3.8 | MP3 ga **ID3 metadata + cover art** embed (ffmpeg) — chiroyli player ko'rinishi | music_search.py |

## FAZA 4 — Ishonchlilik + kuzatuv (yarim kun)

| # | Ish |
|---|-----|
| 4.1 | `user_results` va `_yt_format_cache` ni Redis ga ko'chirish (TTL bilan) — restart-safe |
| 4.2 | Platforma bo'yicha muvaffaqiyat/xato statistikasi (Redis counter) + `/health` admin komandasi |
| 4.3 | Ketma-ket 10 ta xato bo'lsa adminga avto-alert (Instagram sessionid o'ldi, WARP tushdi, ...) |
| 4.4 | MemoryStorage → RedisStorage (FSM restart-safe) |

## FAZA 5 — Tezkor deploy (server load ~32 muammosi)

| # | Ish |
|---|-----|
| 5.1 | **GitHub Actions build** → image GHCR ga push → serverda faqat `docker pull` (build serverda emas!) — deploy 10 daqiqa → 1 daqiqa |
| 5.2 | Dockerfile multi-stage + requirements layer cache |

---

## Tavsiya etilgan tartib

```
1-kun:  FAZA 1 (tezlik) + 2.1 universal fallback     ← eng katta seziladigan farq
2-kun:  FAZA 2 qolgani (platformalar)
3-kun:  FAZA 3 (musiqa qidiruv)
4-kun:  FAZA 4 + 5 (ishonchlilik + deploy)
```

**Kutilayotgan natija:**
- Yuklash tezligi: o'rtacha 2–4x tez (aria2c + local Bot API + pool)
- Katta fayllar: 50MB limit → 2GB, upload 5–10x tez
- Musiqa qidiruv: 5–15s → <1.5s, sifatliroq natijalar
- Platformalar: ~17 ta → 1800+ (universal fallback)
- Takroriy so'rovlar: deyarli bir zumda (kengaytirilgan cache)

---

# ✅ BAJARILGAN ISHLAR (1-bosqich, 2026-08-31)

## Tezlik
- **Local Bot API server** (`aiogram/telegram-bot-api`) qo'shildi — 50MB → 2GB, `file://` zero-copy upload
- **aria2c** yt-dlp ga ulandi (video + musiqa, proxysiz yo'lda) — `-x8 -s8 -k1M`
- **Global httpx HTTP/2 client** (pool 100 conn, keepalive 40) — har so'rovda TLS handshake yo'q
- **uvloop** event loop
- **DOWNLOAD_EXECUTOR** (16 thread) — yt-dlp default pool'ni bloklamaydi
- `http_chunk_size` 10MB, `concurrent_fragment_downloads` 8/16
- Fayllar shared volume da (`/shared_media`) — Bot API server to'g'ridan-to'g'ri diskdan o'qiydi

## Platformalar
- **Universal fallback** — oq ro'yxatdagi 17 ta emas, har qanday http(s) URL yt-dlp ga boradi (1800+ sayt). Qora ro'yxat: t.me, github, wikipedia va h.k.
- Yangi nomli platformalar: Threads, SoundCloud, Twitch, VK, OK.ru, Rutube, Bilibili, Tumblr, 9GAG, LinkedIn, Imgur, Streamable, Rumble, Odysee, BitChute
- **Twitter/X**: fxtwitter API tez yo'l (carousel + video variants)
- **TikTok**: rasm-karusel (photo slide) postlar qo'llandi
- **Instagram**: sessionid o'lsa ham public postlar uchun embed fallback
- **Spotify / Apple Music** havolasi → musiqa pipeline (og:title parse → YTMusic dan yuklash)

## Musiqa (jonli tarmoqda sinovdan o'tkazildi ✅)
- **3 manba parallel**: Deezer + iTunes API + YouTube (tez HTML qidiruv)
- **YouTube tez qidiruv** — `ytInitialData` parse, ~1.2s (yt-dlp `ytsearch` 5–15s o'rniga);
  server IP bloklansa avtomatik WARP proxy orqali
- **iTunes Search API** — bepul, kalitsiz, ~0.7s
- **ytmusicapi** — zaxira (ba'zi IP larda YouTube Music consent talab qiladi, shuning uchun asosiy emas)
- **Redis qidiruv cache** (6 soat) — takroriy qidiruv bir zumda
- YouTube sarlavhalarini tozalash: "Artist - Title" ajratish, `(Official Video)`/`#hashtag` olib tashlash
- Sinov natijasi: `"Yulduz Usmonova Onamga aytmang"` → **1.20s**, 10 ta toza natija (avval 5–15s)
- Fayl nomi `Artist - Title.m4a` (Telegram player chiroyli ko'rsatadi)
- **Inline mode** (`@tinchrobot qo'shiq`) — cache'dagi musiqalar istalgan chatda

---

# ✅ BAJARILGAN ISHLAR (2-bosqich — FAZA 4 & 5)

## Ishonchlilik (FAZA 4)
- **`utils/health.py`** — platforma bo'yicha muvaffaqiyat/xato hisoblagichlari Redis da
  (kunlik, 30 kun saqlanadi) + o'rtacha yuklash vaqti
- **`/health` (yoki `/holat`)** admin komandasi — har bir platforma foizi, tezligi,
  ketma-ket xatolar, hamda Cobalt / bgutil / WARP xizmatlari holati
- **Avto-alert**: ketma-ket 8 ta xatodan keyin adminlarga xabar (1 soat cooldown,
  spam bo'lmaydi). Platformaga xos maslahat bilan — masalan Instagram uchun
  "sessionid eskirgan", YouTube uchun "WARP/bgutil ni restart qiling"
- **`utils/state_store.py`** — Redis do'koni, Redis yiqilsa xotiraga tushadi:
  - `user_results` (qidiruv natijalari, 1 soat)
  - `_yt_format_cache` (YouTube sifat tanlash, 15 daqiqa)
  - `_video_file_ids` (videodan musiqa tugmasi, 1 soat)
  - Shazam natijalari (30 daqiqa)
  → **bot restart bo'lsa tugmalar o'lmaydi**
- **FSM → RedisStorage2** (db=1, 7 kun TTL) — admin panel state'lari restart-safe
- **Handler tartibi tuzatildi**: `/health` musiqa qidiruvining catch-all
  `@dp.message_handler()` ига tushib qolardi — endi undan oldin ro'yxatdan o'tadi

## Chart komandalarida topilgan jiddiy tezlik xatosi 🐛
`/tiktok`, `/top`, `/new` callback **filtri** ichida sayt scrape qilinardi:
```python
@dp.callback_query_handler(lambda x: x.data in [i['id'] for i in world_music()])
```
Ya'ni botdagi **har bir callback query** 3 ta sinxron HTTP scrape + BeautifulSoup
parse'ni ishga tushirar va event loop'ni to'liq bloklardi. Tuzatildi:
- Chart natijalari **1 soat cache** (Redis)
- Scraping **executor** da (event loop bloklanmaydi)
- Manba yiqilsa — **Deezer Charts API** zaxira
- To'g'ridan-to'g'ri MP3 havolasi bo'lsa `direct:` tez yo'l bilan yuboriladi
- Yangi `/chart` komandasi — Deezer global top
- Natijalar umumiy qidiruv sahifasini ishlatadi (pagination + cache + yuklash tayyor)

## Tezkor deploy (FAZA 5)
- **`.github/workflows/build.yml`** — push'da GitHub Actions image ni build qilib
  GHCR ga yuboradi (buildx + GHA cache)
- **`deploy.sh`** — serverda: `git pull` → `docker compose pull` → `up -d` → prune.
  **Serverda build yo'q** — load 30+ bo'lsa ham deploy ~1 daqiqa (avval 10+ daqiqa)
- `docker-compose.yml` da `image: ghcr.io/davronovuz/tiinchrobot:latest` +
  `pull_policy: always` (lokal build kerak bo'lsa `docker compose build bot`)
- Dockerfile: `PYTHONUNBUFFERED=1`, layer tartibi cache uchun optimallashtirildi

## Qolgan ixtiyoriy ishlar
- ID3 metadata + cover art embed (ffmpeg) — player'da chiroyliroq ko'rinish
- Shazam natijasiga "o'xshash qo'shiqlar" tugmasi (Deezer related API)
- Uzbek chart uchun maxsus manba (hozir scraper + Deezer zaxira)

## ⚠️ Deploy tartibi

### Bir martalik tayyorgarlik
1. `.env` da `API_ID` va `API_HASH` bo'lishi shart (local Bot API uchun)
2. **Cloud API dan chiqish** — bu qilinmasa local server 401 beradi:
   ```
   curl "https://api.telegram.org/bot<TOKEN>/logOut"
   ```
3. BotFather → `/setinline` — inline mode yoqish
4. GitHub: Settings → Actions → Workflow permissions → **Read and write** (GHCR push uchun)
5. Serverda GHCR ga kirish (image private bo'lsa):
   ```
   echo <GITHUB_PAT> | docker login ghcr.io -u davronovuz --password-stdin
   ```

### Har safar deploy
```bash
git push            # GitHub Actions build qiladi (~3-5 daqiqa, serverga yuk yo'q)
# serverda:
./deploy.sh         # git pull + docker pull + up -d  (~1 daqiqa)
```

Birinchi marta yoki Actions kutmasdan lokal build kerak bo'lsa:
`docker compose up -d --build`

### Muammo bo'lsa
- Local Bot API'ni o'chirish: `.env` da `BOT_API_URL=` (bo'sh) → Pyrogram avtomatik zaxira
- Platforma holati: botda `/health`
- Loglar: `docker compose logs -f bot`
