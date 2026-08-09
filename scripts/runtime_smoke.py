#!/usr/bin/env python3
"""Offline end-to-end smoke test for the HTTP/task/source/subtitle/LLM path.

This deliberately does not exercise CUDA or a real remote video site. It uses a
fake yt-dlp executable and a local OpenAI-compatible HTTP stub so it can verify
service wiring deterministically in CI or a restricted build environment.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


class LLMHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        user = payload.get("messages", [{}, {}])[-1].get("content", "")
        content = "OK" if "连通性测试" in user else "# 集成测试总结\n\n多源视频总结链路运行成功。"
        raw = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_):
        pass


def make_fake_ytdlp(bin_dir: Path) -> None:
    path = bin_dir / "yt-dlp"
    path.write_text(
        """#!/usr/bin/env python3
import json, os, sys
args=sys.argv[1:]
if '--version' in args:
    print('2026.07.04')
    raise SystemExit(0)
if '--dump-single-json' in args:
    print(json.dumps({'id':'demo123','title':'Runtime Smoke Video','uploader':'Smoke Author','duration':42,'webpage_url':'http://127.0.0.1/video/demo','extractor_key':'Youtube'}))
    raise SystemExit(0)
if '--write-subs' in args or '--write-auto-subs' in args:
    out=args[args.index('-o')+1]
    path=out.replace('%(ext)s','zh-Hans.vtt')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path,'w',encoding='utf-8') as f:
        f.write('WEBVTT\\n\\n00:00:00.000 --> 00:00:03.000\\n这是一个运行级集成测试字幕。\\n\\n00:00:03.000 --> 00:00:06.000\\n用于验证多视频源总结流程。\\n')
    raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bilinote-summary-smoke-") as temp:
        root = Path(temp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        make_fake_ytdlp(bin_dir)
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")

        llm = ThreadingHTTPServer(("127.0.0.1", 0), LLMHandler)
        threading.Thread(target=llm.serve_forever, daemon=True).start()
        port = llm.server_address[1]

        config = root / "config.yaml"
        config.write_text(
            f"""server:
  host: 0.0.0.0
  port: 8080
video:
  prefer_subtitle: true
  subtitle_languages: [zh-Hans, zh.*, en.*]
  cookies_file: ""
  allow_private_urls: true
  allow_unlisted_domains: true
  request_timeout_seconds: 5
bilibili:
  prefer_subtitle: true
  cookie: ""
  request_timeout_seconds: 5
  retries: 1
  retry_backoff_seconds: 0.1
asr:
  enabled: false
  model: tiny
  device: cpu
  compute_type: int8
  language: zh
  model_dir: "{root / 'models'}"
  concurrency: 1
llm:
  base_url: "http://127.0.0.1:{port}/v1"
  api_key: mock
  model: smoke-model
  temperature: 0
  max_tokens: 256
  timeout_seconds: 5
  retries: 0
  retry_backoff_seconds: 0.1
summary:
  prompt_file: "{PROJECT / 'prompts' / 'summary.md'}"
  chunk_chars: 4000
  parallel: 1
task:
  concurrency: 1
  retain_days: 7
  cleanup_interval_seconds: 3600
  max_queue_size: 4
  work_dir: "{root / 'data' / 'tasks'}"
  db_path: "{root / 'data' / 'tasks.db'}"
  download_timeout_seconds: 30
  ffmpeg_timeout_seconds: 30
  task_timeout_seconds: 60
  ytdlp_retries: 1
  ytdlp_fragment_retries: 1
system:
  startup_check: true
  fail_startup_on_error: true
""",
            encoding="utf-8",
        )
        os.environ["APP_CONFIG"] = str(config)

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            ready = client.get("/readyz")
            assert ready.status_code == 200, ready.text
            response = client.post("/api/v1/summaries", json={"url": "http://127.0.0.1/video/demo"})
            assert response.status_code == 202, response.text
            task_id = response.json()["id"]
            deadline = time.time() + 8
            item = {}
            while time.time() < deadline:
                item = client.get(f"/api/v1/tasks/{task_id}").json()
                if item["status"] in {"succeeded", "failed", "canceled"}:
                    break
                time.sleep(0.1)
            assert item.get("status") == "succeeded", item
            assert item["platform"] == "youtube"
            assert item["transcript_source"] == "yt-dlp_subtitle"
            assert "集成测试总结" in item["result"]["summary_markdown"]
            for filename in ["metadata.json", "transcript.json", "summary.json", "summary.md", "task.log"]:
                assert (root / "data" / "tasks" / task_id / filename).exists(), filename

        llm.shutdown()
        print("runtime smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
