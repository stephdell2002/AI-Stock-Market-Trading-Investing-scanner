"""Tiny JSON-over-HTTPS helper for the REST data providers.

Standard-library only (urllib) so the package stays dependency-light and so the
call path respects the machine's HTTP(S)_PROXY env vars automatically. API keys
are passed by the caller (from env vars) and never logged.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "watchman/1.0 (+https://github.com/; personal paper-trading tool)"
DEFAULT_TIMEOUT = 15.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    """A REST call failed. `status` is the HTTP code when there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 2,
) -> Any:
    """GET `url?params` and parse JSON. Retries transient statuses (429/5xx)
    with linear backoff. Raises HttpError on failure — callers decide whether a
    given symbol's failure is fatal or just recorded."""
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    full = f"{url}?{query}" if query else url
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, **(headers or {})})

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt < retries:
                last_error = exc
                time.sleep(1.0 * (attempt + 1))
                continue
            body = _safe_body(exc)
            raise HttpError(
                f"HTTP {exc.code} from {_host(url)}"
                f"{f': {body}' if body else ''}",
                status=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < retries:
                last_error = exc
                time.sleep(1.0 * (attempt + 1))
                continue
            raise HttpError(f"request to {_host(url)} failed: {exc}") from exc
    raise HttpError(f"request to {_host(url)} failed after retries: {last_error}")


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc or url
    except ValueError:
        return url


def _safe_body(exc: urllib.error.HTTPError) -> str:
    try:
        text = exc.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""
    # Keep it short; provider error bodies can be verbose HTML.
    return text[:200].replace("\n", " ")
