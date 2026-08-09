import os
import re
import threading
from pathlib import Path
from typing import Any

import yaml

from .schema import AppConfig

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.getenv(m.group(1), ""), value)
    return value


class ConfigManager:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._config = self._load()

    def _read_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"config file not found: {self.path}")
        with self.path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load(self) -> AppConfig:
        return AppConfig.model_validate(_expand_env(self._read_raw()))

    def get(self) -> AppConfig:
        with self._lock:
            return self._config

    def reload(self) -> AppConfig:
        with self._lock:
            self._config = self._load()
            return self._config

    def public_dict(self) -> dict[str, Any]:
        cfg = self.get().model_dump()
        cfg["llm"]["api_key_configured"] = bool(cfg["llm"].pop("api_key", ""))
        cfg["bilibili"]["cookie_configured"] = bool(cfg["bilibili"].pop("cookie", ""))
        cfg["video"]["cookies_file_configured"] = bool(cfg["video"].get("cookies_file", ""))
        # Security credentials are never returned to the browser/API.
        security = cfg.pop("security", {})
        cfg["web_auth_enabled"] = bool(security.get("web_username") and security.get("web_password"))
        return cfg

    def update_ui_settings(self, patch: dict[str, Any]) -> AppConfig:
        """Persist only the small, explicit set of settings exposed by the Web UI."""
        # GPU model/runtime settings intentionally require a process restart.
        # Hot-swapping ASR can leave an old native CTranslate2 model alive while
        # a second model is loaded, causing avoidable GPU OOMs. Security-sensitive
        # source policy also stays file-only.
        if "asr" in patch:
            raise ValueError("ASR settings are restart-required; edit config.yaml and restart the service")
        allowed = {
            "llm": {"base_url", "api_key", "model", "temperature", "max_tokens", "timeout_seconds", "retries"},
            "video": {"prefer_subtitle", "subtitle_languages", "cookies_file", "request_timeout_seconds"},
            "bilibili": {"prefer_subtitle", "cookie", "request_timeout_seconds", "retries"},
            "summary": {"chunk_chars", "parallel"},
        }
        with self._lock:
            raw = self._read_raw()
            for section, values in patch.items():
                if section not in allowed or not isinstance(values, dict):
                    continue
                target = raw.setdefault(section, {})
                for key, value in values.items():
                    if key not in allowed[section]:
                        continue
                    if key in {"api_key", "cookie"} and (value is None or value == ""):
                        continue
                    target[key] = value
            AppConfig.model_validate(_expand_env(raw))
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            try:
                tmp.replace(self.path)
            except OSError:
                # Docker single-file bind mounts cannot always be atomically
                # replaced (EBUSY). Fall back to an in-place write while the
                # process-local lock is held.
                self.path.write_text(tmp.read_text(encoding="utf-8"), encoding="utf-8")
                tmp.unlink(missing_ok=True)
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
            self._config = self._load()
            return self._config
