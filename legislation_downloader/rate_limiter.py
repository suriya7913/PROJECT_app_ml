"""
rate_limiter.py — Adaptive global rate limiter + pre-run probe.

Strategy
--------
AdaptiveRateLimiter
    A single leaky-bucket shared across ALL worker threads.
    Even with many parallel workers, the API sees at most
    ``1 / interval`` requests per second in total.

    When any worker receives HTTP 429:
      1. A ``threading.Event`` is cleared → all other workers block.
      2. The inter-request interval is doubled (up to a configurable max).
      3. After a back-off sleep the event is set → workers resume.
      4. A ``_backing_off`` flag ensures only ONE thread runs the back-off;
         every other thread just waits for the event and returns.

probe_rate_limit()
    Fires rapid request bursts at decreasing intervals to measure the
    real-world rate limit *before* any discovery or download work begins.
    Returns the recommended safe inter-request interval in seconds.
"""

import time
import logging
import threading

import requests

log = logging.getLogger("LegalKGent.ratelimit")


# ── Adaptive rate limiter ──────────────────────────────────────────────────

class AdaptiveRateLimiter:
    """
    Global leaky-bucket rate limiter for all worker threads.

    Parameters
    ----------
    initial_rps : float
        Starting requests-per-second budget across all workers combined.
    slowest_rps : float
        Never slow below this rate, even after repeated 429 back-offs.
        Default 0.2 req/s = one request every 5 seconds (very conservative).
    """

    def __init__(self, initial_rps: float = 5.0, slowest_rps: float = 0.2):
        self._interval     = 1.0 / initial_rps       # current gap between requests
        self._max_interval = 1.0 / slowest_rps        # cap: never go slower than this
        self._lock         = threading.Lock()
        self._next_allowed = 0.0                      # monotonic wall-clock threshold
        self._go           = threading.Event()
        self._go.set()                                # not paused initially
        self._backing_off  = False                    # True while one thread sleeps off a 429
        self._429_count    = 0

    # ── Public interface ───────────────────────────────────────────────────

    @property
    def rps(self) -> float:
        """Current allowed requests per second (across all threads)."""
        return round(1.0 / self._interval, 2)

    def acquire(self):
        """
        Block until it is safe to fire the next HTTP request.
        Call this from every worker thread immediately before each request.
        """
        self._go.wait()          # blocks if a 429 back-off is in progress
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + self._interval

    def on_rate_limited(self, retry_after: float | None = None):
        """
        Call when a worker receives HTTP 429.

        The first thread to call this pauses all workers, doubles the
        interval, sleeps, then resumes. Subsequent threads just wait.
        """
        with self._lock:
            if self._backing_off:
                # Another thread is already handling it — just wait.
                already_handling = True
            else:
                already_handling  = False
                self._backing_off = True
                self._go.clear()  # pause all workers
                self._429_count  += 1
                # Double the interval, but cap at max_interval.
                self._interval = min(self._interval * 2.0, self._max_interval)
                backoff = retry_after if retry_after else max(self._interval * 10, 30)

        if already_handling:
            self._go.wait()   # wait for the owner thread to finish back-off
            return

        log.warning(
            f"⚠  HTTP 429 (#{self._429_count}) — "
            f"pausing {backoff:.0f}s, new rate ≤ {self.rps} req/s"
        )
        time.sleep(backoff)

        with self._lock:
            self._backing_off = False
        self._go.set()        # resume all workers
        log.info(f"▶  Resuming after back-off (rate={self.rps} req/s)")

    def set_rps(self, rps: float):
        """Override the current rate (e.g. after a successful probe)."""
        with self._lock:
            self._interval = max(1.0 / rps, 1.0 / (1.0 / self._max_interval))
        log.info(f"   Rate limiter → {self.rps} req/s")


# ── Pre-run rate-limit probe ───────────────────────────────────────────────

def probe_rate_limit(
    session:    requests.Session,
    probe_url:  str,
    *,
    candidates: list[float] | None = None,
    burst:      int = 6,
    rest_after_429: float = 8.0,
) -> float:
    """
    Measure the minimum safe inter-request interval against the live API.

    Algorithm
    ---------
    For each candidate interval (starting conservatively and speeding up):
      1. Fire ``burst`` requests spaced by that interval.
      2. Count any HTTP 429 responses.
      3. If zero 429s → record as ``safe_wait`` and try the next (faster) one.
      4. If any 429s → stop and return ``safe_wait`` with a 20% safety margin.

    Parameters
    ----------
    session         : A plain requests.Session (retries disabled for the probe).
    probe_url       : URL to hit — should be a small, always-present endpoint.
    candidates      : Inter-request intervals to test, slowest first.
                      Defaults to ``[0.5, 0.3, 0.2, 0.15, 0.1, 0.07, 0.05]``.
    burst           : Number of requests per candidate.
    rest_after_429  : Seconds to wait after hitting a 429 before returning.

    Returns
    -------
    float : Recommended inter-request interval (seconds), with 20% safety margin.
    """
    if candidates is None:
        candidates = [0.5, 0.3, 0.2, 0.15, 0.1, 0.07, 0.05]

    log.info(f"── Rate-limit probe  (burst={burst}, url={probe_url})")

    safe_wait = 1.0   # guaranteed conservative fallback if even 0.5s triggers 429

    for wait in candidates:
        statuses: list[int] = []
        for _ in range(burst):
            time.sleep(wait)
            try:
                r = session.get(probe_url, timeout=10, allow_redirects=True)
                statuses.append(r.status_code)
            except requests.RequestException:
                statuses.append(0)   # connection error — treat as non-429

        ok_count   = statuses.count(200)
        hit_429    = statuses.count(429)
        other      = burst - ok_count - hit_429

        log.info(
            f"   wait={wait:.2f}s → "
            f"200×{ok_count}  429×{hit_429}  other×{other}"
        )

        if hit_429 == 0:
            safe_wait = wait          # this speed is fine; try going faster
        else:
            log.info(f"   ✗ rate-limited at {wait:.2f}s — stopping probe")
            time.sleep(rest_after_429)   # let the server recover
            break

    recommended = round(safe_wait * 1.2, 3)   # add 20% safety margin
    log.info(
        f"   ✓ Safe wait: {safe_wait:.2f}s → "
        f"using {recommended:.3f}s ({1/recommended:.1f} req/s)"
    )
    return recommended
