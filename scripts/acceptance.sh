#!/usr/bin/env bash
set -euo pipefail

SERVICE="${SERVICE:-bilinote-summary}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${APP_PORT:-8080}}"

ok() { printf '[OK] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
run() { printf '\n> %s\n' "$*"; "$@"; }

command -v docker >/dev/null 2>&1 || fail "docker command not found"
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable"

docker inspect "$SERVICE" >/dev/null 2>&1 || fail "container $SERVICE is not running/created"

run docker exec "$SERVICE" python --version
run docker exec "$SERVICE" yt-dlp --version
run docker exec "$SERVICE" ffmpeg -version
run docker exec "$SERVICE" nvidia-smi
run docker exec "$SERVICE" python -c 'import ctranslate2; n=ctranslate2.get_cuda_device_count(); print("CTranslate2 CUDA devices:", n); raise SystemExit(0 if n > 0 else 2)'

command -v curl >/dev/null 2>&1 || fail "curl command not found"
run curl -fsS "$BASE_URL/healthz"
printf '\n'
run curl -fsS "$BASE_URL/readyz"
printf '\n'

ok "container, dependencies, CUDA and readiness checks passed"
printf 'Next: open %s/settings and run Test LLM + Load Whisper model, then submit one real video URL.\n' "$BASE_URL"
