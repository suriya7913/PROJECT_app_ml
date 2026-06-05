"""
registry.py — Thread-safe URI store + Atom feed parser.

URIRegistry
    Deduplicates discovered legislation entries by URI path.
    All public mutation methods are thread-safe via a lock so that
    multiple discovery workers can write concurrently without collisions.

parse_atom_feed()
    Parses an Atom XML response from legislation.gov.uk into a list of
    typed legislation dicts and an optional next-page URL.
"""

import re
import logging
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict

log = logging.getLogger("LegalKGent.registry")


# ── Atom feed parser ───────────────────────────────────────────────────────

def parse_atom_feed(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    """
    Parse one page of a legislation.gov.uk Atom feed.

    Returns
    -------
    entries  : list of dicts, each with keys: uri, title, type, year, number
    next_url : URL of the next feed page, or None if this is the last page

    Notes
    -----
    • Entry IDs are normalised from full URLs to path-only form:
        https://www.legislation.gov.uk/id/ukpga/2024/3  →  /ukpga/2024/3
    • Entries that don't match the expected /{type}/{year}/{number} pattern
      (e.g. schedule anchors or unexpected formats) are silently skipped.
    """
    entries:  list[dict] = []
    next_url: str | None = None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        log.debug(f"Atom parse error: {exc}")
        return entries, next_url

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # ── Next-page link ──────────────────────────────────────────────────────
    for link in root.findall("atom:link", ns):
        if link.get("rel") == "next":
            href = link.get("href", "")
            if href:
                # Ensure the next URL requests the feed format
                if "data.feed" not in href:
                    href = href.rstrip("/") + "/data.feed"
                next_url = href
            break

    # ── Entry list ──────────────────────────────────────────────────────────
    for entry in root.findall("atom:entry", ns):
        id_el = entry.find("atom:id", ns)
        if id_el is None:
            continue

        raw  = (id_el.text or "").strip()

        # Normalise URL → path
        path = re.sub(r"^https?://www\.legislation\.gov\.uk", "", raw)
        path = re.sub(r"^/id/", "/", path)

        # Must match /{type}/{year}/{number}
        match = re.match(r"^/([a-z]+)/(\d+)/(\d+)$", path)
        if not match:
            continue

        leg_type = match.group(1)
        year     = int(match.group(2))
        number   = int(match.group(3))

        title_el = entry.find("atom:title", ns)
        title    = (title_el.text or "").strip() if title_el is not None else ""

        entries.append({
            "uri":    path,
            "title":  title,
            "type":   leg_type,
            "year":   year,
            "number": number,
        })

    return entries, next_url


# ── URI Registry ───────────────────────────────────────────────────────────

class URIRegistry:
    """
    Thread-safe, deduplicated store of all discovered legislation URIs.

    Items are keyed by their URI path (e.g. ``/ukpga/2024/3``).  Adding the
    same URI a second time (from a different discovery layer) is a no-op.

    Classification
    --------------
    PRIMARY_TYPES   : Full Acts of Parliament (ukpga, asp, …)
    SECONDARY_TYPES : UK Statutory Instruments (uksi)
    NON_UK_SI_TYPES : Welsh, Scottish, NI instruments — excluded from corpus
    """

    PRIMARY_TYPES   = {"ukpga", "asp", "anaw", "mwa", "nia", "ukcm"}
    SECONDARY_TYPES = {"uksi"}
    NON_UK_SI_TYPES = {"ssi", "wsi", "nisr", "ukmo", "ukmd"}

    def __init__(self):
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._lock  = threading.Lock()

    # ── Mutation (thread-safe) ─────────────────────────────────────────────

    def add(self, item: dict) -> bool:
        """
        Add a legislation item.  Returns True if it was new, False if the URI
        was already present.  Thread-safe.
        """
        key = item["uri"]
        with self._lock:
            if key in self._items:
                return False
            self._items[key] = item
            return True

    def add_seed(self, leg_type: str, year: int, number: int, title: str):
        """Convenience wrapper — build and add a seed item directly."""
        self.add({
            "uri":    f"/{leg_type}/{year}/{number}",
            "title":  title,
            "type":   leg_type,
            "year":   year,
            "number": number,
        })

    def filter_by_year(self, min_si_year: int) -> int:
        """
        Remove items that should not be in the corpus:
          • All non-UK SI types (Welsh SIs, Scottish SIs, NI instruments)
          • UK SIs older than *min_si_year*
          • Primary Acts are never removed.

        Returns the number of items removed.  Thread-safe.
        """
        with self._lock:
            to_remove = [
                key for key, item in self._items.items()
                if (
                    item["type"] in self.NON_UK_SI_TYPES
                    or (
                        item["type"] in self.SECONDARY_TYPES
                        and item["year"] < min_si_year
                    )
                )
            ]
            for key in to_remove:
                del self._items[key]

        log.info(
            f"  Year filter (uksi ≥ {min_si_year}, drop non-UK SIs): "
            f"removed {len(to_remove)}, kept {len(self._items)}"
        )
        return len(to_remove)

    # ── Read-only views (thread-safe snapshots) ────────────────────────────

    @property
    def items(self) -> list[dict]:
        """Snapshot of all items as a plain list (safe to iterate)."""
        with self._lock:
            return list(self._items.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def acts(self) -> list[dict]:
        """Primary legislation (Acts of Parliament)."""
        return [i for i in self.items if i["type"] in self.PRIMARY_TYPES]

    def sis(self) -> list[dict]:
        """UK Statutory Instruments."""
        return [i for i in self.items if i["type"] in self.SECONDARY_TYPES]
