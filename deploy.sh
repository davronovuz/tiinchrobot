#!/usr/bin/env bash
# Serverda ishga tushiriladi — build QILMAYDI, faqat tayyor image ni tortadi.
# Server load 30+ bo'lganda ham 1 daqiqada deploy bo'ladi.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Kodni yangilash"
git pull --ff-only

echo "==> GHCR dan yangi image ni tortish"
docker compose pull bot

echo "==> Qayta ishga tushirish"
docker compose up -d

echo "==> Eski image larni tozalash"
docker image prune -f

echo "==> Holat"
docker compose ps
echo
echo "Loglar:  docker compose logs -f bot"
