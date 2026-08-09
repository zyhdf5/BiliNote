from pathlib import Path

import yaml

from app.config.manager import ConfigManager


def test_config_update_falls_back_when_atomic_replace_is_unavailable(tmp_path: Path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("{}\n", encoding="utf-8")
    manager = ConfigManager(str(path))

    def busy_replace(self, target):
        raise OSError("device or resource busy")

    monkeypatch.setattr(Path, "replace", busy_replace)
    cfg = manager.update_ui_settings({"llm": {"model": "smoke-model"}})
    assert cfg.llm.model == "smoke-model"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["llm"]["model"] == "smoke-model"
