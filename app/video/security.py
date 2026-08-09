import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from app.config.schema import VideoConfig


def _unsafe_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower().strip().lstrip(".").rstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def validate_http_url(url: str, *, allow_private: bool = False) -> str:
    value = url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http/https video URLs are supported")
    if not parsed.hostname:
        raise ValueError("video URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("video URL userinfo credentials are not supported")
    if len(value) > 4096:
        raise ValueError("video URL is too long")
    host = parsed.hostname.lower().rstrip(".")
    if allow_private:
        return value
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("private/local video URLs are disabled")
    try:
        if _unsafe_ip(host):
            raise ValueError("private/local video URLs are disabled")
    except ValueError as exc:
        if "private/local" in str(exc):
            raise
    return value


def validate_source_policy(url: str, cfg: VideoConfig) -> str:
    value = validate_http_url(url, allow_private=cfg.allow_private_urls)
    if cfg.allow_unlisted_domains:
        return value
    host = (urlparse(value).hostname or "").lower().rstrip(".")
    if not any(_domain_matches(host, domain) for domain in cfg.allowed_domains):
        raise ValueError(
            f"video host {host!r} is not in video.allowed_domains; "
            "add it explicitly or set allow_unlisted_domains=true"
        )
    return value


async def validate_resolved_target(url: str, *, allow_private: bool = False) -> str:
    value = validate_http_url(url, allow_private=allow_private)
    if allow_private:
        return value
    host = urlparse(value).hostname or ""
    try:
        ipaddress.ip_address(host)
        return value
    except ValueError:
        pass

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM),
            timeout=5,
        )
    except (TimeoutError, socket.gaierror) as exc:
        raise ValueError(f"cannot resolve video host {host}: {exc}") from exc

    addresses = {info[4][0].split("%", 1)[0] for info in infos}
    if not addresses:
        raise ValueError(f"cannot resolve video host {host}")
    for address in addresses:
        try:
            if _unsafe_ip(address):
                raise ValueError(f"video host resolves to private/reserved address: {address}")
        except ValueError as exc:
            if "private/reserved" in str(exc):
                raise
    return value
