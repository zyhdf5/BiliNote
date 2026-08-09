# Runtime Hardening Report

Date: 2026-08-09

## Code-review fixes implemented

### P1

- Stored XSS: Markdown is rendered then sanitized with an HTML allowlist (`bleach`); CSP and browser security headers are enabled.
- CSRF: Web forms use a double-submit CSRF token; browser REST writes require same-origin and a matching CSRF header.
- Whisper model-load cancellation: all callers share one shielded native model-load task; cancellation cannot trigger a concurrent second model load.
- ASR hot swap: ASR runtime is created once for the process lifetime. `asr.*` Web/API updates are rejected and require editing `config.yaml` + restart.
- SSRF/source policy: localhost/private/reserved targets and URL userinfo are rejected by default; a domain allowlist is enabled by default. Canonical extractor URLs are revalidated before reuse.
- Capacity/resource bounds: bounded task queue, maximum video duration, yt-dlp max download size, and minimum free disk threshold.
- Default exposure: Docker Compose publishes `127.0.0.1:${APP_PORT}:8080`; Basic Auth remains available for remote/reverse-proxy deployments.

### P2

- Bilibili subtitle semantics: disabling the dedicated Bilibili subtitle API no longer disables generic yt-dlp subtitle fallback.
- Generic yt-dlp socket timeout moved to `video.request_timeout_seconds`; Bilibili keeps its dedicated timeout.
- Persistent task log redaction now covers embedded URLs, Authorization values and common Bilibili/token fields; task logs are size bounded.
- `/readyz` uses a TTL-cached fresh `SystemChecker` result instead of a permanent startup snapshot. Manual GPU/ASR checks also update shared readiness state.
- ASR/CTranslate2/yt-dlp dependencies are exact-pinned for reproducible GPU builds.
- Task timeout is explicitly documented as a **soft timeout** during native Whisper work. Strict hard timeout requires ASR process isolation.

## Multi-source ingestion

- Dedicated `BilibiliSource` with native metadata/player subtitle APIs and yt-dlp fallback.
- Generic `GenericYtDlpSource` for YouTube, Douyin, Kuaishou, TikTok and explicitly allowed additional domains.
- Manual subtitles before automatic captions, then audio + Faster-Whisper fallback.
- Netscape cookies file supported for non-Bilibili yt-dlp sources.

## Runtime/container baseline

- CUDA 12.x + cuDNN runtime image.
- Python 3.11+ runtime.
- `faster-whisper==1.2.1`.
- `ctranslate2==4.8.1`.
- `yt-dlp==2026.7.4`.
- Per-task SQLite/file persistence, cleanup, subprocess process-group cancellation and Docker log rotation.

## Automated validation

```text
python -m compileall -q app tests scripts run.py
PASS

python -m pytest -q
25 passed

python scripts/runtime_smoke.py
runtime smoke: PASS
```

Additional Web smoke validation:

```text
GET /          -> 200 + CSP + CSRF cookie
GET /settings  -> 200 + external settings.js
POST /settings with invalid CSRF -> 403
```

The runtime smoke starts the real FastAPI lifespan/task worker and uses a deterministic fake yt-dlp plus a local OpenAI-compatible HTTP stub to execute:

```text
POST /api/v1/summaries
 -> GenericYtDlpSource
 -> metadata probe
 -> subtitle extraction
 -> VTT parsing
 -> LLM summary request
 -> SQLite + JSON/Markdown persistence
 -> GET task => succeeded
```

## Environment-limited validation

This execution environment does not provide a Docker daemon or NVIDIA GPU, so these are deployment-machine acceptance items rather than claimed passes:

1. `docker compose build --pull`
2. CUDA/CTranslate2 discovery inside the built container
3. actual Faster-Whisper `large-v3` GPU model load/transcription
4. live extraction against Bilibili/YouTube/Douyin/Kuaishou/TikTok

## Deployment-machine acceptance

```bash
cp .env.example .env
docker compose build --pull
docker compose up -d
./scripts/acceptance.sh
```

Windows PowerShell:

```powershell
./scripts/acceptance.ps1
```

For remote access, configure `WEB_USERNAME` / `WEB_PASSWORD` and deliberately change the Compose port binding or place the service behind an authenticated reverse proxy. For public/multi-tenant deployments, also enforce network-level egress rules that block private/link-local/cloud-metadata ranges; application validation cannot sandbox every internal request made by a third-party extractor.
