"""Tiny stdlib HTTP helpers.

Deliberately dependency-free: the job that holds the API key runs no
third-party code, so a compromised PyPI package can't read it.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

TIMEOUT = 60
USER_AGENT = "ai-lottery-picks (+https://github.com/tbeason/ai-lottery-picks)"

# Anything that looks like an API key. Applied to everything we print or save.
_KEY_PATTERNS = [
    re.compile(r"sk-or-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{8,}"),
]


class HttpError(Exception):
    def __init__(self, status: Optional[int], message: str):
        self.status = status
        super().__init__(redact(message))


def redact(text: Any) -> str:
    text = str(text)
    for var in ("OPENROUTER_API_KEY",):
        val = os.environ.get(var)
        if val and len(val) >= 8:
            text = text.replace(val, "[REDACTED]")
    for pat in _KEY_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _request(req: urllib.request.Request, timeout: int) -> Any:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace")
        raise HttpError(e.code, f"HTTP {e.code} from {req.host}: {body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(None, f"network error for {req.host}: {e}") from None
    except json.JSONDecodeError as e:
        raise HttpError(None, f"bad JSON from {req.host}: {e}") from None


def get_json(url: str, params: Optional[Dict[str, str]] = None, timeout: int = TIMEOUT) -> Any:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout)


def get_text(url: str, timeout: int = TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(getattr(e, "code", None), f"fetch failed for {req.host}: {e}") from None


def post_json(url: str, body: Dict, headers: Dict[str, str], timeout: int = TIMEOUT) -> Any:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers})
    return _request(req, timeout)
