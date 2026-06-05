"""
manifest.py — Save a JSON download manifest.

Records what was fetched, when, and summary statistics so you can
audit or resume a download run without re-crawling.
"""

import json
import logging
from datetime import datetime

from .registry import URIRegistry

log = logging.getLogger("LegalKGent.manifest")


def save_manifest(
    registry: URIRegistry,
    stats:    dict,
    elapsed:  float,
    path:     str,
) -> None:
    """
    Write a JSON manifest to *path*.

    Parameters
    ----------
    registry : The fully-populated URI registry after all downloads.
    stats    : Download result counts, e.g.::

                   {
                       "legislation": {"ok": 120, "skip": 5, "fail": 2},
                       "effects":     {"ok": 118, "skip": 7, "fail": 2},
                       "cases":       0,
                   }

    elapsed  : Total wall-clock time for the run (seconds).
    path     : File path to write (created or overwritten).
    """
    manifest = {
        "download_time":    datetime.now().isoformat(),
        "domain":           "UK Transportation Law",
        "api_version":      "v3 (Official OpenAPI)",
        "elapsed_seconds":  round(elapsed, 1),
        "discovery_layers": [
            "subject_path_search",
            "title_keyword_search",
            "year_enumeration",
            "seed_fallback",
        ],
        "stats":       stats,
        "total_uris":  len(registry),
        "acts_count":  len(registry.acts()),
        "sis_count":   len(registry.sis()),
        "items": [
            {
                "uri":    item["uri"],
                "title":  item["title"],
                "type":   item["type"],
                "year":   item["year"],
                "number": item["number"],
            }
            for item in registry.items
        ],
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    log.info(f"  Manifest → {path}  ({len(registry)} URIs)")
