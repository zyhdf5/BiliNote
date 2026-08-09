# Code Review Fixes

All findings from the 2026-08-09 review have been addressed or explicitly bounded.

| Finding | Status | Implementation |
|---|---|---|
| Stored XSS in rendered LLM Markdown | Fixed | Markdown -> Bleach allowlist -> `safe`; CSP/security headers |
| SSRF redirect/rebinding exposure | Mitigated | domain allowlist + private IP/DNS checks + canonical URL revalidation; network egress isolation documented as required for public/multi-tenant use |
| Duplicate GPU model load after cancellation | Fixed | shared shielded `_model_load_task` |
| ASR hot reload can create two GPU models | Fixed | single process-lifetime ASR runtime; Web/API ASR updates rejected |
| Unbounded task queue/video resources | Fixed | `max_queue_size`, `max_video_duration_seconds`, `max_download_mb`, `min_free_disk_gb` |
| Default unauthenticated network exposure | Fixed default | Compose binds host loopback; Basic Auth recommended/available for remote exposure |
| Missing CSRF | Fixed | form token + browser API same-origin/CSRF checks |
| Timeout claimed as hard timeout | Clarified | explicit soft-timeout semantics; process isolation required for strict hard kill |
| Bilibili `prefer_subtitle=false` disables all subtitle paths | Fixed | only native API disabled; generic yt-dlp subtitle path remains |
| Child-process logs may leak signed URLs/secrets | Fixed | centralized line redaction + bounded task log |
| Stale readiness snapshot | Fixed | TTL refresh + manual checks update state |
| Generic source timeout coupled to Bilibili | Fixed | `video.request_timeout_seconds` |
| Floating GPU/runtime dependencies | Fixed | exact pins for faster-whisper/CTranslate2/yt-dlp |
