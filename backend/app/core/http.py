import gzip
import http.cookiejar as http_cookiejar
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

THROTTLE_STATUS = {403, 406, 408, 416, 425, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when a URL could not be fetched after all retries."""


@dataclass
class Response:
    url: str
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("Content-Type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")

    def json(self) -> Any:
        # strict=False tolerates a literal control character inside a string,
        # which is invalid JSON but is what wien.gv.at's index actually emits: a
        # concert programme with a raw newline in its description. Nothing else
        # about the grammar is relaxed, so genuinely malformed JSON still raises.
        return json.loads(self.text, strict=False)


def encode_query(params: list[tuple[str, str]]) -> str:
    return "&".join(
        f"{key}={urllib.parse.quote(str(value), safe='')}" for key, value in params
    )


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        try:
            return gzip.decompress(raw)
        except Exception:  # noqa: BLE001 - some servers mislabel
            return raw
    if encoding == "deflate":
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(raw, wbits)
            except zlib.error:
                continue
    return raw


class HttpClient:
    def __init__(
        self,
        delay: float = 0.5,
        timeout: float = 30.0,
        retries: int = 3,
        user_agent: str = DEFAULT_UA,
        extra_headers: dict[str, str] | None = None,
        use_cookies: bool = False,
    ) -> None:
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-AT,de;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            **(extra_headers or {}),
        }
        # One scraped site holds its search results server-side against the
        # session, so paging only works if cookies are carried between requests.
        handlers = []
        if use_cookies:
            self.cookies = http_cookiejar.CookieJar()
            handlers.append(urllib.request.HTTPCookieProcessor(self.cookies))
        self._opener = urllib.request.build_opener(*handlers)
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self.request_count = 0
        self.bytes_fetched = 0
        self.status_counts: dict[int, int] = {}

    def _wait_turn(self) -> None:
        if self.delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            sleep_for = self._next_allowed - now
            self._next_allowed = max(now, self._next_allowed) + self.delay
        if sleep_for > 0:
            time.sleep(sleep_for)

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        data: bytes | None = None,
    ) -> Response:
        request_headers = {**self.headers, **(headers or {})}
        last_error: Exception | None = None

        for attempt in range(self.retries):
            self._wait_turn()
            req = urllib.request.Request(
                url, data=data, headers=request_headers, method=method
            )
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = _decompress(resp.read(), resp.headers.get("Content-Encoding"))
                    with self._lock:
                        self.request_count += 1
                        self.bytes_fetched += len(raw)
                        self.status_counts[resp.status] = (
                            self.status_counts.get(resp.status, 0) + 1
                        )
                    return Response(
                        url=resp.url,
                        status=resp.status,
                        body=raw,
                        headers=dict(resp.headers),
                    )
            except urllib.error.HTTPError as exc:
                with self._lock:
                    self.request_count += 1
                    self.status_counts[exc.code] = (
                        self.status_counts.get(exc.code, 0) + 1
                    )
                last_error = FetchError(f"HTTP {exc.code} for {url}")
                if exc.code not in THROTTLE_STATUS:
                    break
                backoff = 2.0 * (attempt + 1) ** 2
                logger.warning(
                    "throttled (%s) on %s - backing off %.0fs", exc.code, url, backoff
                )
                time.sleep(backoff)
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))

        raise FetchError(f"failed to fetch {url}: {last_error}")

    def post_form(self, url: str, form: dict[str, str], **kw) -> Response:
        body = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        headers.update(kw.pop("headers", None) or {})
        return self.get(url, headers=headers, method="POST", data=body, **kw)

    def post_json(self, url: str, payload: dict, **kw) -> Response:
        headers = {"Content-Type": "application/json"}
        headers.update(kw.pop("headers", None) or {})
        return self.get(
            url,
            headers=headers,
            method="POST",
            data=json.dumps(payload).encode(),
            **kw,
        )
