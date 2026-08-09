import asyncio
import random
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from app.config.schema import BilibiliConfig
from app.models import Transcript, TranscriptSegment, VideoMeta
from app.video.security import validate_resolved_target

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"
_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")


class BilibiliError(RuntimeError):
    pass


class BilibiliRiskControlError(BilibiliError):
    pass


class BilibiliClient:
    def __init__(self, cfg: BilibiliConfig):
        self.cfg = cfg

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
        if self.cfg.cookie:
            headers["Cookie"] = self.cfg.cookie
        return headers

    async def _request(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            try:
                response = await client.get(url, headers=self._headers(), **kwargs)
                if response.status_code in {412, 429}:
                    last_exc = BilibiliRiskControlError(
                        f"Bilibili risk control HTTP {response.status_code}; configure a valid Cookie/SESSDATA or fall back to yt-dlp"
                    )
                elif response.status_code >= 500:
                    last_exc = BilibiliError(f"Bilibili HTTP {response.status_code}")
                else:
                    response.raise_for_status()
                    return response
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_exc = exc
            if attempt < self.cfg.retries:
                delay = self.cfg.retry_backoff_seconds * (2 ** attempt) + random.uniform(0, 0.25)
                await asyncio.sleep(delay)
        if isinstance(last_exc, BilibiliRiskControlError):
            raise last_exc
        raise BilibiliError(f"Bilibili request failed after retries: {last_exc}")

    async def _resolve_url(self, url: str) -> str:
        if "b23.tv" not in url:
            return url
        current = url
        async with httpx.AsyncClient(follow_redirects=False, timeout=self.cfg.request_timeout_seconds) as client:
            for _hop in range(5):
                response = await client.get(current, headers=self._headers())
                if response.status_code in {412, 429}:
                    raise BilibiliRiskControlError(
                        f"Bilibili risk control HTTP {response.status_code}; configure a valid Cookie/SESSDATA"
                    )
                if 300 <= response.status_code < 400:
                    location = response.headers.get("location")
                    if not location:
                        raise BilibiliError("b23.tv redirect has no Location header")
                    target = urljoin(current, location)
                    host = (urlparse(target).hostname or "").lower()
                    if not (host == "b23.tv" or host.endswith(".b23.tv") or host == "bilibili.com" or host.endswith(".bilibili.com")):
                        raise BilibiliError(f"b23.tv redirected to unexpected host: {host}")
                    # Do not allow a trusted short URL to become an application-level
                    # SSRF hop through DNS/private addressing.
                    await validate_resolved_target(target, allow_private=False)
                    current = target
                    continue
                response.raise_for_status()
                return str(response.url)
        raise BilibiliError("too many b23.tv redirects")

    @staticmethod
    def _part_number(url: str) -> int | None:
        try:
            p = parse_qs(urlparse(url).query).get("p", [None])[0]
            return int(p) if p else None
        except (TypeError, ValueError):
            return None

    async def metadata(self, input_url: str) -> VideoMeta:
        url = await self._resolve_url(input_url)
        match = _BVID_RE.search(url)
        if not match:
            raise ValueError("无法从 URL 中识别 Bilibili BV 号")
        bvid = match.group(0)
        part = self._part_number(url)
        params: dict[str, object] = {"bvid": bvid}
        if part:
            params["p"] = part
        async with httpx.AsyncClient(timeout=self.cfg.request_timeout_seconds) as client:
            response = await self._request(client, "https://api.bilibili.com/x/web-interface/view", params=params)
            payload = response.json()
        code = payload.get("code")
        if code in {-412, -352}:
            raise BilibiliRiskControlError(f"Bilibili risk control API code {code}: {payload.get('message') or ''}")
        if code != 0:
            raise BilibiliError(f"Bilibili view API failed: {payload.get('message') or code}")
        data = payload.get("data") or {}
        pages = data.get("pages") or []
        selected = None
        if pages:
            idx = max(0, (part or 1) - 1)
            if idx >= len(pages):
                idx = 0
            selected = pages[idx]
        cid = (selected or {}).get("cid") or data.get("cid")
        duration = (selected or {}).get("duration") or data.get("duration") or 0
        title = data.get("title") or ""
        if selected and selected.get("part") and len(pages) > 1:
            title = f"{title} - P{(part or 1)} {selected.get('part')}"
        owner = data.get("owner") or {}
        return VideoMeta(
            platform="bilibili",
            url=url,
            video_id=bvid,
            bvid=bvid,
            cid=int(cid) if cid else None,
            part=part,
            title=title,
            author=owner.get("name") or "",
            duration=int(duration or 0),
            cover_url=data.get("pic") or "",
            extractor="bilibili-api",
        )

    async def subtitle(self, meta: VideoMeta) -> Transcript | None:
        if not meta.cid or not meta.bvid:
            return None
        async with httpx.AsyncClient(timeout=self.cfg.request_timeout_seconds) as client:
            response = await self._request(
                client,
                "https://api.bilibili.com/x/player/wbi/v2",
                params={"bvid": meta.bvid, "cid": meta.cid},
            )
            payload = response.json()
            code = payload.get("code")
            if code in {-412, -352}:
                raise BilibiliRiskControlError(f"Bilibili subtitle risk control API code {code}")
            if code != 0:
                return None
            tracks = (((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
            if not tracks:
                return None
            track = self._pick_track(tracks)
            subtitle_url = (track or {}).get("subtitle_url")
            if not subtitle_url:
                return None
            if subtitle_url.startswith("//"):
                subtitle_url = "https:" + subtitle_url
            sub_response = await self._request(client, subtitle_url)
            body = (sub_response.json() or {}).get("body") or []
        segments = [
            TranscriptSegment(
                start=float(item.get("from") or 0),
                end=float(item.get("to") or 0),
                text=(item.get("content") or "").strip(),
            )
            for item in body
            if (item.get("content") or "").strip()
        ]
        if not segments:
            return None
        language = (track or {}).get("lan") or "zh"
        return Transcript(
            source="bilibili_player_api",
            language=language,
            segments=segments,
            text=" ".join(x.text for x in segments),
            raw={"bvid": meta.bvid, "cid": meta.cid, "language": language},
        )

    @staticmethod
    def _pick_track(tracks: list[dict]) -> dict | None:
        def is_zh(track: dict) -> bool:
            lan = str(track.get("lan") or "").lower()
            return lan.startswith("zh") or lan == "ai-zh"

        for track in tracks:
            if is_zh(track) and not track.get("ai_type"):
                return track
        for track in tracks:
            if is_zh(track):
                return track
        return tracks[0] if tracks else None
