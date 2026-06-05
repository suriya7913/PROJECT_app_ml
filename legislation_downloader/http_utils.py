"""
http_utils.py — HTTP session factory and rate-limit-aware request helper.

build_session()
    Creates a requests.Session with auto-retry on connection errors and
    5xx responses.  HTTP 429 is NOT retried here — the AdaptiveRateLimiter
    handles those explicitly.

get_bytes()
    Downloads a URL with rate-limit awareness:
      • Calls limiter.acquire() before each attempt.
      • On 429 → calls limiter.on_rate_limited() and retries.
      • On 404/410 → returns None silently.
      • On network error → retries up to 3 times with exponential back-off.
      • Returns None on all unrecoverable failures.
"""

import time
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .rate_limiter import AdaptiveRateLimiter
from .constants    import HEADERS

log = logging.getLogger("LegalKGent.http")

# Maximum per-request retries for get_bytes() on 429 or network errors.
_MAX_ATTEMPTS = 3


# ── Session factory ────────────────────────────────────────────────────────

def build_session(max_retries: int = 3) -> requests.Session:
    """
    Build a requests.Session pre-configured for legislation.gov.uk.

    Retry policy
    ------------
    Automatically retries on connection-level errors and HTTP 5xx.
    Does NOT retry on 429 — those are handled by AdaptiveRateLimiter
    so that all worker threads pause together rather than each spinning.

    Parameters
    ----------
    max_retries : Low-level urllib3 retry count for transient errors.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    retry = Retry(
        total=max_retries,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],   # 429 handled separately
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=32,
        pool_maxsize=32,
        max_retries=retry,
    )
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


# ── Rate-limit-aware downloader ───────────────────────────────────────────

def get_bytes(
    session:  requests.Session,
    url:      str,
    limiter:  AdaptiveRateLimiter,
    *,
    silent_404: bool = True,
) -> tuple[bytes | None, str]:
    """
    Fetch *url* and return (content, reason), honouring the shared rate limiter.

    Behaviour per status code
    -------------------------
    200          → (content, "ok")
    429          → call limiter.on_rate_limited() (pauses ALL workers) and retry
    404/410/436  → (None, "no_data")   — item exists but no content in this format
    other 4xx/5xx → (None, "error") with a warning logged
    network error → warn, retry; (None, "exhausted") after all attempts

    Notes
    -----
    HTTP 436 is legislation.gov.uk's custom "no data in this format" code —
    it is not a standard HTTP code and signals the same condition as 404.

    Parameters
    ----------
    silent_404 : If True (default), 404/410/436 are not logged.

    Returns
    -------
    (bytes | None, str) — content and one of "ok", "no_data", "error", "exhausted".
    """
    for attempt in range(_MAX_ATTEMPTS):
        limiter.acquire()

        try:
            response = session.get(url, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning(f"Network error (attempt {attempt + 1}/{_MAX_ATTEMPTS}): {exc}  [{url}]")
            time.sleep(wait)
            continue

        # ── Handle response ────────────────────────────────────────────────
        if response.status_code == 200:
            return response.content, "ok"

        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            limiter.on_rate_limited(retry_after)
            continue   # retry after back-off

        if response.status_code in (404, 410, 436):
            # 436 = legislation.gov.uk custom "content not available in this format"
            if not silent_404:
                log.debug(f"HTTP {response.status_code}: {url}")
            return None, "no_data"

        # Any other non-200
        log.warning(f"HTTP {response.status_code}: {url}")
        return None, "error"

    log.warning(f"All {_MAX_ATTEMPTS} attempts exhausted: {url}")
    return None, "exhausted"


# ── Helpers ────────────────────────────────────────────────────────────────

def _parse_retry_after(response: requests.Response) -> float | None:
    """
    Parse the Retry-After header value.
    Returns seconds as float, or None if the header is absent or unparseable.
    """
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        # Header may be an HTTP-date string — fall back to a safe default.
        return 60.0
