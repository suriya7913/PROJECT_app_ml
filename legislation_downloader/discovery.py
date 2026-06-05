"""
discovery.py — Three-layer parallel discovery of transport legislation URIs.

Each layer uses a ThreadPoolExecutor so that slow API pages don't block
the rest of the work.  All workers share one AdaptiveRateLimiter so the
total request rate stays within the probed API budget.

Layer 1A — subject-path search
    One task per subject slug (e.g. "road-traffic").
    URL: GET /{ALL_TYPES}/{subject}/data.feed

Layer 1B — title-keyword search
    One task per title keyword (e.g. "road traffic").
    URL: GET /title/{encoded_keyword}/data.feed

Layer 2  — year enumeration (exhaustive)
    One task per (leg_type, year) combination.
    URL: GET /uksi/{year}/data.feed
    All entries in the year are fetched; YEAR_FILTER_RE is applied
    client-side to keep only transport-related SIs.

discover_all()
    Runs all three layers in sequence (layers are internally parallel).
"""

import logging
import concurrent.futures
from urllib.parse import quote

import requests

from .constants import (
    BASE_URL, ALL_TYPES,
    TRANSPORT_SUBJECTS, TITLE_KEYWORDS,
    YEAR_ENUM_RANGE, YEAR_ENUM_TYPES, YEAR_FILTER_RE,
)
from .registry     import URIRegistry, parse_atom_feed
from .rate_limiter import AdaptiveRateLimiter
from .http_utils   import get_bytes

# Import the page cap from the project-level config.
# (legislation_downloader is a sub-package of the project root.)
try:
    from config import MAX_PAGES_PER_FEED
except ImportError:
    MAX_PAGES_PER_FEED = 300   # safe fallback if run in isolation

log = logging.getLogger("LegalKGent.discovery")


# ── Feed exhaustion helper ─────────────────────────────────────────────────

def _exhaust_feed(
    session:      requests.Session,
    limiter:      AdaptiveRateLimiter,
    registry:     URIRegistry,
    start_url:    str,
    *,
    label:        str = "",
    title_filter  = None,          # compiled re.Pattern or None
) -> int:
    """
    Follow an Atom feed page-by-page until exhausted or MAX_PAGES_PER_FEED.

    Parameters
    ----------
    title_filter : Optional compiled regex.  When provided, only entries
                   whose title matches are added to the registry.
                   Used by Layer 2 (year enumeration) to filter client-side.

    Returns the number of net-new items added to the registry.
    """
    url   = start_url
    added = 0
    page  = 0

    while url and page < MAX_PAGES_PER_FEED:
        data, _ = get_bytes(session, url, limiter)
        if not data:
            break

        entries, next_url = parse_atom_feed(data)

        for entry in entries:
            if title_filter and not title_filter.search(entry.get("title", "")):
                continue
            if registry.add(entry):
                added += 1

        url  = next_url
        page += 1

    if label and added:
        log.debug(f"  {label}: +{added}  (pages={page})")
    return added


# ── Layer 1A: subject-path search ──────────────────────────────────────────

def discover_by_subject(
    session:  requests.Session,
    limiter:  AdaptiveRateLimiter,
    registry: URIRegistry,
    workers:  int = 8,
) -> int:
    """
    Fire one feed-walk per subject slug, all in parallel.
    Returns total new items added across all subjects.
    """
    log.info(
        f"── Layer 1A: subject search  "
        f"({len(TRANSPORT_SUBJECTS)} subjects, {workers} workers)"
    )
    before = len(registry)

    def _task(subject: str) -> int:
        url = f"{BASE_URL}/{ALL_TYPES}/{subject}/data.feed"
        return _exhaust_feed(
            session, limiter, registry, url,
            label=f"subject/{subject}",
        )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="disc-subj",
    ) as pool:
        list(pool.map(_task, TRANSPORT_SUBJECTS))

    added = len(registry) - before
    log.info(f"  Layer 1A TOTAL: +{added} new  (registry={len(registry)})")
    return added


# ── Layer 1B: title-keyword search ─────────────────────────────────────────

def discover_by_title(
    session:  requests.Session,
    limiter:  AdaptiveRateLimiter,
    registry: URIRegistry,
    workers:  int = 8,
) -> int:
    """
    Fire one feed-walk per title keyword, all in parallel.
    Returns total new items added across all keywords.
    """
    log.info(
        f"── Layer 1B: title search  "
        f"({len(TITLE_KEYWORDS)} keywords, {workers} workers)"
    )
    before = len(registry)

    def _task(keyword: str) -> int:
        url = f"{BASE_URL}/title/{quote(keyword)}/data.feed"
        return _exhaust_feed(
            session, limiter, registry, url,
            label=f"title/{keyword}",
        )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="disc-title",
    ) as pool:
        list(pool.map(_task, TITLE_KEYWORDS))

    added = len(registry) - before
    log.info(f"  Layer 1B TOTAL: +{added} new  (registry={len(registry)})")
    return added


# ── Layer 2: year-enumeration search ───────────────────────────────────────

def discover_by_year_enum(
    session:  requests.Session,
    limiter:  AdaptiveRateLimiter,
    registry: URIRegistry,
    workers:  int = 8,
) -> int:
    """
    For each (leg_type, year) pair, download ALL legislation in that year
    then apply YEAR_FILTER_RE client-side to keep only transport-related SIs.
    All year-slots run in parallel.

    Returns total new items added.
    """
    tasks = [(t, y) for t in YEAR_ENUM_TYPES for y in YEAR_ENUM_RANGE]
    log.info(
        f"── Layer 2: year enumeration  "
        f"({len(tasks)} year-slots, {workers} workers)"
    )
    before = len(registry)

    def _task(args: tuple[str, int]) -> tuple[str, int, int]:
        leg_type, year = args
        url   = f"{BASE_URL}/{leg_type}/{year}/data.feed"
        added = _exhaust_feed(
            session, limiter, registry, url,
            label=f"{leg_type}/{year}",
            title_filter=YEAR_FILTER_RE,
        )
        return leg_type, year, added

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="disc-year",
    ) as pool:
        for leg_type, year, added in pool.map(_task, tasks):
            if added:
                log.debug(f"    {leg_type}/{year}: +{added}")

    added = len(registry) - before
    log.info(f"  Layer 2 TOTAL: +{added} new  (registry={len(registry)})")
    return added


# ── Master entry point ─────────────────────────────────────────────────────

def discover_all(
    session:  requests.Session,
    limiter:  AdaptiveRateLimiter,
    registry: URIRegistry,
    workers:  int = 8,
) -> dict[str, int]:
    """
    Run all three discovery layers and return per-layer new-item counts.

    Layers run sequentially (1A → 1B → 2) but each is internally parallel.
    This ordering avoids redundant work: Layer 1A seeds the registry so
    later layers just need to deduplicate rather than re-download.

    Returns
    -------
    dict with keys: "subjects", "titles", "years"
    """
    return {
        "subjects": discover_by_subject  (session, limiter, registry, workers),
        "titles":   discover_by_title    (session, limiter, registry, workers),
        "years":    discover_by_year_enum(session, limiter, registry, workers),
    }
