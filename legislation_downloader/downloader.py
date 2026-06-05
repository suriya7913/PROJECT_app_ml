"""
downloader.py — Parallel downloading of legislation XML and effects feeds.

download_legislation()
    Downloads the primary XML file for every item in the registry.
    • Acts  → raw_leg_dir   (e.g. data/raw_legislation/)
    • SIs   → raw_si_dir    (e.g. data/raw_statutory_instruments/)
    Skips files that already exist on disk.

download_effects()
    Downloads the Atom effects/changes feed for each item.
    URL: /changes/affected/{type}/{year}/{number}/data.feed
    Output: amendments_dir  (e.g. data/amendments/)
    Skips files that already exist on disk.

Both functions use a shared ThreadPoolExecutor and AdaptiveRateLimiter.
A _Counter helper logs progress every N items so long runs are easy to
monitor without flooding the log.
"""

import os
import logging
import threading
import concurrent.futures

import requests

from .constants    import BASE_URL
from .registry     import URIRegistry
from .rate_limiter import AdaptiveRateLimiter
from .http_utils   import get_bytes

log = logging.getLogger("LegalKGent.downloader")

# How often to log a progress line (every N items processed).
_LOG_EVERY = 50


# ── Progress counter ───────────────────────────────────────────────────────

class _Counter:
    """
    Thread-safe OK / SKIP / FAIL counter with periodic progress logging.

    Logs a one-line summary every ``log_every`` items so you can track
    long download runs without waiting for the final summary.
    """

    def __init__(self, total: int, label: str, log_every: int = _LOG_EVERY):
        self.ok = self.skip = self.no_data = self.fail = 0
        self._done    = 0
        self._total   = total
        self._label   = label
        self._log_every = log_every
        self._lock    = threading.Lock()

    def bump(self, kind: str):
        """Increment a counter and log if a checkpoint is reached."""
        with self._lock:
            if   kind == "ok":      self.ok      += 1
            elif kind == "skip":    self.skip    += 1
            elif kind == "no_data": self.no_data += 1
            else:                   self.fail    += 1
            self._done += 1
            if self._done % self._log_every == 0:
                pct = 100 * self._done // self._total
                log.info(
                    f"  [{self._label}] {self._done}/{self._total} ({pct}%)  "
                    f"OK={self.ok}  SKIP={self.skip}  "
                    f"NO_DATA={self.no_data}  FAIL={self.fail}"
                )


# ── File I/O helper ────────────────────────────────────────────────────────

def _save(data: bytes, filepath: str) -> bool:
    """Write *data* to *filepath*, creating parent directories if needed."""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as fh:
            fh.write(data)
        return True
    except OSError as exc:
        log.error(f"Cannot write {filepath}: {exc}")
        return False


# ── Legislation XML download ───────────────────────────────────────────────

def download_legislation(
    session:     requests.Session,
    limiter:     AdaptiveRateLimiter,
    registry:    URIRegistry,
    raw_leg_dir: str,
    raw_si_dir:  str,
    workers:     int = 16,
) -> dict[str, int]:
    """
    Download the primary XML for every item in the registry.

    URL pattern: /{type}/{year}/{number}/data.xml

    Parameters
    ----------
    raw_leg_dir : Output folder for primary Acts.
    raw_si_dir  : Output folder for Statutory Instruments.
    workers     : ThreadPoolExecutor size.

    Returns
    -------
    dict with keys "ok", "skip", "fail".
    """
    items = registry.items
    ctr   = _Counter(len(items), "legislation")
    log.info(f"── Downloading legislation XML  ({len(items)} items, {workers} workers)")

    def _process(item: dict):
        leg_type = item["type"]
        year     = item["year"]
        number   = item["number"]

        # Route Acts vs SIs to different output directories.
        out_dir  = raw_leg_dir if leg_type in URIRegistry.PRIMARY_TYPES else raw_si_dir
        filename = f"{leg_type}_{year}_{number}.xml"
        filepath = os.path.join(out_dir, filename)

        if os.path.exists(filepath):
            ctr.bump("skip")
            return

        url        = f"{BASE_URL}/{leg_type}/{year}/{number}/data.xml"
        data, reason = get_bytes(session, url, limiter)

        if data and data.strip().startswith(b"<"):
            _save(data, filepath)
            ctr.bump("ok")
            log.debug(f"  ✓ {filename}")
        elif reason == "no_data":
            ctr.bump("no_data")
        else:
            ctr.bump("fail")
            if data:
                log.warning(f"  ✗ unexpected content: {url}  →  {data[:80]!r}")

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="dl-leg",
    ) as pool:
        pool.map(_process, items)

    log.info(
        f"  Legislation DONE: "
        f"OK={ctr.ok}  SKIP={ctr.skip}  NO_DATA={ctr.no_data}  FAIL={ctr.fail}"
    )
    return {"ok": ctr.ok, "skip": ctr.skip, "no_data": ctr.no_data, "fail": ctr.fail}


# ── Effects / amendments feed download ────────────────────────────────────

def download_effects(
    session:       requests.Session,
    limiter:       AdaptiveRateLimiter,
    registry:      URIRegistry,
    amendments_dir: str,
    workers:       int = 16,
) -> dict[str, int]:
    """
    Download the legislative effects (amendments) Atom feed for each item.

    URL pattern: /changes/affected/{type}/{year}/{number}/data.feed

    The effects feed records which other legislation has amended, repealed,
    or otherwise modified each piece of legislation.

    Parameters
    ----------
    amendments_dir : Output folder for effects feeds.
    workers        : ThreadPoolExecutor size.

    Returns
    -------
    dict with keys "ok", "skip", "fail".
    """
    items = registry.items
    ctr   = _Counter(len(items), "effects")
    log.info(f"── Downloading effects feeds  ({len(items)} items, {workers} workers)")

    def _process(item: dict):
        leg_type = item["type"]
        year     = item["year"]
        number   = item["number"]

        filename = f"{leg_type}_{year}_{number}_effects.xml"
        filepath = os.path.join(amendments_dir, filename)

        if os.path.exists(filepath):
            ctr.bump("skip")
            return

        url          = f"{BASE_URL}/changes/affected/{leg_type}/{year}/{number}/data.feed"
        data, reason = get_bytes(session, url, limiter)

        if data and data.strip().startswith(b"<"):
            _save(data, filepath)
            ctr.bump("ok")
        elif reason == "no_data":
            ctr.bump("no_data")
        else:
            ctr.bump("fail")

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="dl-eff",
    ) as pool:
        pool.map(_process, items)

    log.info(
        f"  Effects DONE: "
        f"OK={ctr.ok}  SKIP={ctr.skip}  NO_DATA={ctr.no_data}  FAIL={ctr.fail}"
    )
    return {"ok": ctr.ok, "skip": ctr.skip, "no_data": ctr.no_data, "fail": ctr.fail}
