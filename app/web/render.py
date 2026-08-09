import html

try:
    import bleach
except ImportError:  # pragma: no cover - Docker/runtime dependency
    bleach = None

try:
    import markdown as _markdown
except ImportError:  # defensive fallback for minimal test environments
    _markdown = None

_ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em",
    "a", "hr", "table", "thead", "tbody", "tr", "th", "td",
}
_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "code": ["class"],
}
_ALLOWED_PROTOCOLS = {"http", "https", "mailto"}


def render_markdown_safe(value: str) -> str:
    if not value:
        return ""
    if _markdown is None:
        return "<pre>" + html.escape(value) + "</pre>"
    rendered = _markdown.markdown(value, extensions=["fenced_code", "tables"])
    if bleach is None:
        # Fail closed if the sanitizer dependency is unexpectedly absent.
        return "<pre>" + html.escape(value) + "</pre>"
    return bleach.clean(
        rendered,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
