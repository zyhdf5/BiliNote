import html
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from app.config.schema import BilibiliConfig, TaskConfig, VideoConfig
from app.models import Transcript, TranscriptSegment, VideoMeta
from app.utils.logging import append_task_log
from app.utils.process import ProcessExecutionError, run_logged_process

_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_VTT_TIME_RE = re.compile(
    r"(?P<start>(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")


def _is_bilibili(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "b23.tv" or host.endswith(".b23.tv") or host == "bilibili.com" or host.endswith(".bilibili.com")


def _platform(data: dict) -> str:
    raw = str(data.get("extractor_key") or data.get("extractor") or "generic").lower()
    webpage = str(data.get("webpage_url") or data.get("original_url") or "").lower()
    if "bilibili" in raw or "bilibili.com" in webpage or "b23.tv" in webpage:
        return "bilibili"
    if "youtube" in raw or "youtu.be" in webpage or "youtube.com" in webpage:
        return "youtube"
    if "douyin" in raw or "douyin.com" in webpage:
        return "douyin"
    if "tiktok" in raw or "tiktok.com" in webpage:
        return "tiktok"
    if "kuaishou" in raw or "kuaishou.com" in webpage:
        return "kuaishou"
    return re.sub(r"[^a-z0-9_.-]+", "-", raw).strip("-") or "generic"


def _parse_timestamp(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    raise ValueError(f"invalid VTT timestamp: {value}")


def parse_vtt(path: Path, *, source: str, language: str | None = None) -> Transcript | None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    segments: list[TranscriptSegment] = []
    i = 0
    previous_text = ""
    while i < len(lines):
        match = _VTT_TIME_RE.search(lines[i])
        if not match:
            i += 1
            continue
        start = _parse_timestamp(match.group("start"))
        end = _parse_timestamp(match.group("end"))
        i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            value = _TAG_RE.sub("", lines[i]).strip()
            if value:
                text_lines.append(value)
            i += 1
        text = html.unescape(" ".join(text_lines)).strip()
        # Auto captions sometimes repeat the full previous cue. Avoid bloating
        # the transcript while keeping genuinely repeated spoken phrases.
        if text and text != previous_text:
            segments.append(TranscriptSegment(start=start, end=end, text=text))
            previous_text = text
        i += 1
    if not segments:
        return None
    return Transcript(
        source=source,
        language=language,
        segments=segments,
        text=" ".join(x.text for x in segments),
        raw={"subtitle_file": path.name},
    )


class YtDlpDownloader:
    def __init__(self, video_cfg: VideoConfig, bili_cfg: BilibiliConfig, task_cfg: TaskConfig):
        self.video_cfg = video_cfg
        self.bili_cfg = bili_cfg
        self.task_cfg = task_cfg

    def _bilibili_cookie_file(self, url: str) -> str | None:
        if not _is_bilibili(url):
            return None
        cookie = self.bili_cfg.cookie.strip()
        if not cookie:
            return None
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write("# Netscape HTTP Cookie File\n")
        for pair in cookie.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            tmp.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}\n")
        tmp.close()
        try:
            os.chmod(tmp.name, 0o600)
        except OSError:
            pass
        return tmp.name

    def _cookie_args(self, url: str) -> tuple[list[str], str | None]:
        temporary = self._bilibili_cookie_file(url)
        if temporary:
            return ["--cookies", temporary], temporary
        generic = self.video_cfg.cookies_file.strip()
        if generic and Path(generic).is_file():
            return ["--cookies", generic], None
        return [], None

    def _common_args(self, url: str, cookie_args: list[str]) -> list[str]:
        args = [
            "--ignore-config",
            *cookie_args,
            "--no-playlist",
            "--socket-timeout", str(
                self.bili_cfg.request_timeout_seconds if _is_bilibili(url) else self.video_cfg.request_timeout_seconds
            ),
            "--retries", str(self.task_cfg.ytdlp_retries),
            "--fragment-retries", str(self.task_cfg.ytdlp_fragment_retries),
            "--retry-sleep", "exp=1:5",
        ]
        if _is_bilibili(url):
            args.extend(["--referer", "https://www.bilibili.com/"])
        return args

    @staticmethod
    def _executable() -> str:
        # Resolve once per invocation so Windows PATHEXT shims (.cmd/.bat) and
        # project-local wrappers selected by PATH are passed to CreateProcess
        # as an absolute path. A bare name can otherwise skip those shims.
        return shutil.which("yt-dlp") or "yt-dlp"

    @staticmethod
    def _cleanup_temp_cookie(path: str | None) -> None:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def probe_metadata(self, url: str, log_path: Path) -> VideoMeta:
        cookie_args, temp_cookie = self._cookie_args(url)
        try:
            cmd = [self._executable(), *self._common_args(url, cookie_args), "--dump-single-json", "--skip-download", url]
            output = await run_logged_process(
                cmd,
                timeout=min(self.task_cfg.download_timeout_seconds, 300),
                log_path=log_path,
                label="yt-dlp-probe",
                capture_stdout=True,
                log_stdout=False,
            )
            lines = [x.strip() for x in output.splitlines() if x.strip().startswith("{")]
            if not lines:
                raise RuntimeError("yt-dlp metadata probe returned no JSON")
            data = json.loads(lines[-1])
            webpage_url = str(data.get("webpage_url") or data.get("original_url") or url)
            identifier = str(data.get("id") or "")
            match = _BVID_RE.search(identifier) or _BVID_RE.search(webpage_url) or _BVID_RE.search(url)
            platform = _platform(data)
            return VideoMeta(
                platform=platform,
                url=webpage_url,
                video_id=identifier or (match.group(0) if match else ""),
                bvid=match.group(0) if match else None,
                title=str(data.get("title") or ""),
                author=str(data.get("uploader") or data.get("channel") or data.get("creator") or ""),
                duration=int(float(data.get("duration") or 0)),
                cover_url=str(data.get("thumbnail") or ""),
                extractor=str(data.get("extractor_key") or data.get("extractor") or ""),
            )
        finally:
            self._cleanup_temp_cookie(temp_cookie)

    async def fetch_subtitle(self, url: str, output_dir: Path, log_path: Path) -> Transcript | None:
        """Prefer uploader subtitles, then fall back to automatic captions."""
        subtitle_dir = output_dir / "subtitles"
        languages = ",".join(self.video_cfg.subtitle_languages) or "all"

        async def attempt(flag: str, label: str, source_name: str) -> Transcript | None:
            shutil.rmtree(subtitle_dir, ignore_errors=True)
            subtitle_dir.mkdir(parents=True, exist_ok=True)
            cookie_args, temp_cookie = self._cookie_args(url)
            try:
                cmd = [
                    self._executable(),
                    *self._common_args(url, cookie_args),
                    "--skip-download",
                    flag,
                    "--sub-langs", languages,
                    "--sub-format", "vtt/best",
                    "--convert-subs", "vtt",
                    "-o", str(subtitle_dir / "subtitle.%(ext)s"),
                    url,
                ]
                try:
                    await run_logged_process(
                        cmd,
                        timeout=min(self.task_cfg.download_timeout_seconds, 600),
                        log_path=log_path,
                        label=label,
                    )
                except ProcessExecutionError as exc:
                    append_task_log(log_path, f"{label} unavailable: {exc}")
                    return None

                candidates = list(subtitle_dir.glob("*.vtt"))
                if not candidates:
                    return None

                def rank(path: Path) -> tuple[int, str]:
                    name = path.name.lower()
                    priorities = ["zh-hans", "zh-cn", ".zh.", "zh-hant", "zh-tw", "en"]
                    for idx, marker in enumerate(priorities):
                        if marker in name:
                            return idx, name
                    return len(priorities), name

                for path in sorted(candidates, key=rank):
                    language = None
                    pieces = path.name.split(".")
                    if len(pieces) >= 3:
                        language = pieces[-2]
                    transcript = parse_vtt(path, source=source_name, language=language)
                    if transcript:
                        return transcript
                return None
            finally:
                self._cleanup_temp_cookie(temp_cookie)

        manual = await attempt("--write-subs", "yt-dlp-subtitle", "yt-dlp_subtitle")
        if manual:
            return manual
        automatic = await attempt("--write-auto-subs", "yt-dlp-auto-subtitle", "yt-dlp_auto_subtitle")
        return automatic

    async def download_audio(self, url: str, output_dir: Path, log_path: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_template = str(output_dir / "audio.%(ext)s")
        cookie_args, temp_cookie = self._cookie_args(url)
        try:
            cmd = [
                self._executable(),
                *self._common_args(url, cookie_args),
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "64K",
                "--max-filesize", f"{self.task_cfg.max_download_mb}M",
                "--match-filter", f"duration <= {self.task_cfg.max_video_duration_seconds}",
                "-o", output_template,
                url,
            ]
            await run_logged_process(
                cmd,
                timeout=self.task_cfg.download_timeout_seconds,
                log_path=log_path,
                label="yt-dlp",
            )
            path = output_dir / "audio.mp3"
            if not path.exists():
                candidates = [p for p in output_dir.glob("audio.*") if p.name != "audio.wav"]
                if not candidates:
                    raise FileNotFoundError("yt-dlp finished but audio output was not found")
                path = candidates[0]
            max_bytes = self.task_cfg.max_download_mb * 1024 * 1024
            if path.stat().st_size > max_bytes:
                try:
                    path.unlink()
                finally:
                    raise RuntimeError(
                        f"downloaded audio exceeds configured limit {self.task_cfg.max_download_mb} MiB"
                    )
            return path
        finally:
            self._cleanup_temp_cookie(temp_cookie)
