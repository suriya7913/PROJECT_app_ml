#!/usr/bin/env python3
"""
Script 1 — Download UK Transport Legislation
=============================================

Orchestrates five phases:

  Phase 0 — Connectivity check
             Confirms legislation.gov.uk is reachable before doing anything.

  Phase 1 — Rate-limit probe
             Fires rapid test bursts to measure the real-world API rate
             limit.  Sets the AdaptiveRateLimiter before any real work starts.

  Phase 2 — Discover legislation URIs  (fully parallel)
             Layer 1A : subject-path search  (e.g. "road-traffic")
             Layer 1B : title-keyword search (e.g. "road traffic")
             Layer 2  : year enumeration     (uksi/2000 … uksi/2026)
             All layers run their tasks in parallel worker threads.

  Phase 3 — Filter
             Remove non-UK SIs and SIs older than MIN_SI_YEAR.

  Phase 4 — Download XML + effects  (fully parallel)
             Legislation XML and effects feeds downloaded concurrently.
             Workers share the adaptive rate limiter — if any worker
             receives HTTP 429 the whole pool pauses and backs off.

  Phase 5 — Save manifest
             JSON record of everything downloaded.

Usage
-----
    python 1_download_legislation.py

Configuration
-------------
    Edit config.py for paths, worker counts, and retry limits.
    Search targets (subjects, keywords, years) are in:
        legislation_downloader/constants.py
"""

import os
import time
import logging

import requests

# ── Project config (paths, worker counts, retry settings) ─────────────────
from config import (
    RAW_LEGISLATION_DIR,
    RAW_CASELAW_DIR,
    RAW_SI_DIR,
    AMENDMENTS_DIR,
    MANIFEST_FILE,
    MAX_RETRIES,
    NUM_WORKERS,
)

# ── Downloader sub-package ─────────────────────────────────────────────────
from legislation_downloader.constants    import BASE_URL, HEADERS, SEED_ACTS, MIN_SI_YEAR, PROBE_URL
from legislation_downloader.rate_limiter import AdaptiveRateLimiter, probe_rate_limit
from legislation_downloader.http_utils   import build_session
from legislation_downloader.registry     import URIRegistry
from legislation_downloader.discovery    import discover_all
from legislation_downloader.downloader   import download_legislation, download_effects
from legislation_downloader.manifest     import save_manifest


# ══════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════
# Format: timestamp  LEVEL    logger-name              message
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)-24s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("LegalKGent")


# ══════════════════════════════════════════════════════════════════════════
# Output directories
# ══════════════════════════════════════════════════════════════════════════
for _dir in [RAW_LEGISLATION_DIR, RAW_CASELAW_DIR, RAW_SI_DIR, AMENDMENTS_DIR]:
    os.makedirs(_dir, exist_ok=True)


# ══════════════════════════════════════════════════════════════════════════
# Phase 0 — Connectivity check
# ══════════════════════════════════════════════════════════════════════════

def _connectivity_check() -> bool:
    """
    Confirm legislation.gov.uk is reachable and returning valid XML.
    Returns True on success, False on any failure.
    """
    test_url = f"{BASE_URL}/ukpga/1988/52/data.xml"
    log.info("Phase 0 — Connectivity check")
    try:
        r = requests.get(test_url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and r.content.strip().startswith(b"<"):
            log.info(f"  ✓ Connected to legislation.gov.uk  ({len(r.content):,} bytes)")
            return True
        log.error(f"  ✗ Unexpected response: HTTP {r.status_code}")
        return False
    except requests.RequestException as exc:
        log.error(f"  ✗ Cannot reach legislation.gov.uk: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def run():
    t_start  = time.time()
    registry = URIRegistry()

    # ── Seed acts ────────────────────────────────────────────────────────
    log.info(f"Loading {len(SEED_ACTS)} seed acts")
    for leg_type, year, number, title in SEED_ACTS:
        registry.add_seed(leg_type, year, number, title)

    # ── Phase 0: connectivity ────────────────────────────────────────────
    if not _connectivity_check():
        log.error("Aborting — no network access to legislation.gov.uk")
        return

    with build_session(max_retries=MAX_RETRIES) as session:

        # ── Phase 1: rate-limit probe ────────────────────────────────────
        log.info("Phase 1 — Rate-limit probe")
        safe_interval = probe_rate_limit(session, PROBE_URL)
        safe_rps      = 1.0 / safe_interval if safe_interval > 0 else 3.0
        limiter       = AdaptiveRateLimiter(initial_rps=safe_rps)
        log.info(f"  Limiter ready: {limiter.rps} req/s  (interval={safe_interval:.3f}s)")

        # ── Phase 2: discover URIs ───────────────────────────────────────
        log.info("Phase 2 — Discover legislation URIs")
        layer_counts = discover_all(
            session, limiter, registry,
            workers=NUM_WORKERS,
        )

        log.info(
            f"\n{'='*60}\n"
            f"  DISCOVERY COMPLETE\n"
            f"  Total unique URIs  : {len(registry)}\n"
            f"  Primary Acts       : {len(registry.acts())}\n"
            f"  SIs / instruments  : {len(registry.sis())}\n"
            f"  Layer 1A (subjects): +{layer_counts['subjects']}\n"
            f"  Layer 1B (titles)  : +{layer_counts['titles']}\n"
            f"  Layer 2  (years)   : +{layer_counts['years']}\n"
            f"{'='*60}"
        )

        # ── Phase 3: filter old / non-UK SIs ────────────────────────────
        log.info("Phase 3 — Filter old SIs")
        registry.filter_by_year(MIN_SI_YEAR)
        log.info(
            f"  After filter: {len(registry)} URIs  "
            f"(Acts={len(registry.acts())}, SIs={len(registry.sis())})"
        )

        # ── Phase 4: download XML + effects ──────────────────────────────
        log.info("Phase 4 — Download XML + effects feeds")
        dl_leg = download_legislation(
            session, limiter, registry,
            raw_leg_dir=RAW_LEGISLATION_DIR,
            raw_si_dir=RAW_SI_DIR,
            workers=NUM_WORKERS * 4,
        )
        dl_eff = download_effects(
            session, limiter, registry,
            amendments_dir=AMENDMENTS_DIR,
            workers=NUM_WORKERS * 4,
        )

    # ── Phase 5: manifest ────────────────────────────────────────────────
    elapsed = time.time() - t_start
    save_manifest(
        registry,
        stats={
            "legislation": dl_leg,
            "effects":     dl_eff,
            "cases":       0,         # case law handled by 3_download_caselaw.py
        },
        elapsed=elapsed,
        path=MANIFEST_FILE,
    )

    # ── Final summary ────────────────────────────────────────────────────
    print(f"""
{'='*60}
ALL DONE  ({elapsed:.0f}s  ≈  {elapsed / 60:.1f} min)
{'='*60}
  Total URIs discovered : {len(registry)}
  Legislation XML
    Downloaded (OK)     : {dl_leg['ok']}
    Skipped (exists)    : {dl_leg['skip']}
    No XML available    : {dl_leg['no_data']}
    Failed (real error) : {dl_leg['fail']}
  Effects feeds
    Downloaded (OK)     : {dl_eff['ok']}
    Skipped (exists)    : {dl_eff['skip']}
    No effects data     : {dl_eff['no_data']}
    Failed (real error) : {dl_eff['fail']}
  Manifest              : {MANIFEST_FILE}
{'='*60}
""")


if __name__ == "__main__":
    print("  Running Legislation Download (Parallel Exhaustive Crawl)")
    run()
