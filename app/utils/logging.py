import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SENSITIVE_QUERY_KEYS = {
    "access_token", "api_key", "apikey", "auth", "authorization", "credential",
    "key", "password", "secret", "sig", "signature", "token",
}
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_AUTH_RE = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+")
_COOKIE_RE = re.compile(r"(?i)\b(SESSDATA|bili_jct|DedeUserID__ckMd5|access_token|api_key|token)=([^;\s]+)")
_MAX_TASK_LOG_BYTES = 4 * 1024 * 1024
_KEEP_TASK_LOG_BYTES = 2 * 1024 * 1024


def redact_url(value: str) -> str:
    """Mask common credential-like query values before persistent logging."""
    if not value.startswith(("http://", "https://")):
        return value
    try:
        parts = urlsplit(value)
        query = []
        for key, val in parse_qsl(parts.query, keep_blank_values=True):
            query.append((key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else val))
        # Drop fragments; signed URLs sometimes leak credentials there as well.
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    except Exception:
        return value


def redact_text(value: str) -> str:
    text = _AUTH_RE.sub(r"\1***", value)
    text = _COOKIE_RE.sub(lambda m: f"{m.group(1)}=***", text)
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), text)


def _trim_log_if_needed(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size <= _MAX_TASK_LOG_BYTES:
            return
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - _KEEP_TASK_LOG_BYTES))
            tail = f.read()
        marker = b"--- task log truncated to bound persistent log size ---\n"
        path.write_bytes(marker + tail)
    except OSError:
        # Logging must never fail the video task.
        pass


def append_task_log(path: Path, message: str, *, source: str = "app") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _trim_log_if_needed(path)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    safe_message = redact_text(message.rstrip())
    with path.open("a", encoding="utf-8", errors="replace") as f:
        f.write(f"{ts} [{source}] {safe_message}\n")


def tail_text(path: Path, max_bytes: int = 64 * 1024) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    return data.decode("utf-8", errors="replace")
