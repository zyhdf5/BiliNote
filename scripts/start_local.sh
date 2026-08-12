#!/usr/bin/env bash
# 本地（非 Docker）启动：加载 .env、使用便携 FFmpeg 与 config.local.yaml
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
source .env
set +a

export PATH="$PWD/.venv/Scripts:$PWD/tools/ffmpeg/bin:$PATH"
export APP_CONFIG="${APP_CONFIG:-config.local.yaml}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

exec .venv/Scripts/python run.py
