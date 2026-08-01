"""webfetch tool — port of opencode `tool/webfetch.ts`."""
from __future__ import annotations

import html.parser
import re
import urllib.error
import urllib.request
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .base import ToolContext, ToolResult

MAX_RESPONSE_SIZE = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120

_DESCRIPTION = """- Fetches content from a specified URL
- Takes a URL and optional format as input
- Fetches the URL content, converts to requested format (markdown by default)
- Returns the content in the specified format
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - Format options: "markdown" (default), "text", or "html"
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
"""

_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


class WebFetchParams(BaseModel):
    url: str = Field(description="The URL to fetch content from")
    format: Literal["text", "markdown", "html"] = Field(
        default="markdown",
        description="The format to return the content in (text, markdown, or html). Defaults to markdown.",
    )
    timeout: Optional[int] = Field(
        default=None,
        description="Optional timeout in seconds (max 120)",
    )


class WebFetchTool:
    name = "webfetch"
    description = _DESCRIPTION
    params = WebFetchParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = WebFetchParams(**args)
        url = p.url.strip()
        if url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        if not url.startswith("https://") and not url.startswith("http://"):
            return ToolResult.error("webfetch", "URL must start with http:// or https://")

        try:
            ctx.ask("webfetch", [url], ["*"])
        except Exception as exc:
            return ToolResult.error("webfetch", f"permission denied: {exc}")

        timeout = min(p.timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
        accept = {
            "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
            "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
            "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, */*;q=0.1",
        }.get(p.format, "*/*")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            ),
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            data, content_type = _http_get(url, headers, timeout)
        except Exception as exc:
            return ToolResult.error("webfetch", str(exc))

        if len(data) > MAX_RESPONSE_SIZE:
            return ToolResult.error("webfetch", "Response too large (exceeds 5MB limit)")

        mime = (content_type or "").split(";")[0].strip().lower()
        title = f"{url} ({content_type or 'unknown'})"

        if mime in _IMAGE_MIMES:
            import base64

            b64 = base64.b64encode(data).decode("ascii")
            return ToolResult.success(
                "webfetch",
                "Image fetched successfully",
                attachments=[{"type": "file", "mime": mime, "url": f"data:{mime};base64,{b64}"}],
            )

        text = data.decode("utf-8", errors="replace")
        if p.format == "html":
            body = text
        elif p.format == "text":
            body = _extract_text_from_html(text) if "html" in mime else text
        else:  # markdown
            body = _html_to_markdown(text) if "html" in mime else text

        # soft truncate very large text for the model context
        if len(body) > 100_000:
            body = body[:100_000] + "\n\n...[truncated]"

        return ToolResult.success(title, body)


def _http_get(url: str, headers: dict, timeout: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cl = resp.headers.get("Content-Length")
            if cl and int(cl) > MAX_RESPONSE_SIZE:
                raise RuntimeError("Response too large (exceeds 5MB limit)")
            data = resp.read(MAX_RESPONSE_SIZE + 1)
            if len(data) > MAX_RESPONSE_SIZE:
                raise RuntimeError("Response too large (exceeds 5MB limit)")
            return data, resp.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as exc:
        # Cloudflare challenge retry with short UA (opencode behaviour)
        if exc.code == 403 and exc.headers.get("cf-mitigated") == "challenge":
            headers = {**headers, "User-Agent": "sleuth"}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(MAX_RESPONSE_SIZE + 1), resp.headers.get("Content-Type") or ""
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"fetch failed: {exc.reason}") from exc


class _HTMLTextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if self._skip or tag in ("script", "style", "noscript", "iframe", "object", "embed"):
            self._skip += 1

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self._parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def _extract_text_from_html(raw: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", raw)
    return parser.text()


def _html_to_markdown(raw: str) -> str:
    """Lightweight HTML→markdown (stdlib only; not full Turndown parity)."""
    # headings
    out = raw
    for i in range(6, 0, -1):
        out = re.sub(
            rf"<h{i}[^>]*>(.*?)</h{i}>",
            lambda m, n=i: "\n" + "#" * n + " " + _strip_tags(m.group(1)) + "\n",
            out,
            flags=re.I | re.S,
        )
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"</p>", "\n\n", out, flags=re.I)
    out = re.sub(r"<li[^>]*>", "- ", out, flags=re.I)
    out = re.sub(r"</li>", "\n", out, flags=re.I)
    out = re.sub(
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        lambda m: f"[{_strip_tags(m.group(2))}]({m.group(1)})",
        out,
        flags=re.I | re.S,
    )
    out = re.sub(
        r"<code[^>]*>(.*?)</code>",
        lambda m: f"`{_strip_tags(m.group(1))}`",
        out,
        flags=re.I | re.S,
    )
    out = re.sub(
        r"<pre[^>]*>(.*?)</pre>",
        lambda m: "\n```\n" + _strip_tags(m.group(1)) + "\n```\n",
        out,
        flags=re.I | re.S,
    )
    out = _strip_tags(out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)
