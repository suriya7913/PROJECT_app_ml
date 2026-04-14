#!/usr/bin/env python3
"""


This script does three things:
  1. DISCOVER — find all relevant legislation URIs via subject, title, and year searches
  2. FILTER   — remove non-UK SIs and old SIs to keep the corpus manageable
  3. DOWNLOAD — fetch the actual XML for each discovered URI + its effects feed
"""

import os
import re
import json
import time
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import OrderedDict
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import (
    RAW_LEGISLATION_DIR, RAW_CASELAW_DIR, RAW_SI_DIR, AMENDMENTS_DIR, MANIFEST_FILE,
    MAX_RETRIES, DOWNLOAD_RATE_LIMIT_WAIT, MAX_PAGES_PER_FEED
)

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
# This sets up console output so you can see what's happening while it runs.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("LegalKGent")


# ─────────────────────────────────────────────
# CREATE OUTPUT DIRECTORIES
# ─────────────────────────────────────────────
# These folders will hold the downloaded XML files.
for d in [RAW_LEGISLATION_DIR, RAW_CASELAW_DIR, RAW_SI_DIR, AMENDMENTS_DIR]:
    os.makedirs(d, exist_ok=True)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
BASE_URL = "https://www.legislation.gov.uk"
HEADERS = {
    "User-Agent": "LegalKGent-Research-Project/3.0 (university-research)",
    "Accept":     "application/xml, application/atom+xml, */*",
}

# Minimum year for Statutory Instruments — SIs older than this will be filtered out.
MIN_SI_YEAR = 2000


# ─────────────────────────────────────────────
# SEARCH CONFIGURATION
# ─────────────────────────────────────────────

# Layer 1A: Subject path search
# The legislation.gov.uk API lets you search by subject slug:
#   GET /{type}/{subject}/data.feed
TRANSPORT_SUBJECTS = [
    "transport",
    "road-traffic",
    "road-safety",
    "highways",
    "motor-vehicles",
    "driving-licences",
    "railways",
    "aviation",
    "shipping",
    "public-transport",
    "vehicle-excise",
    "traffic-regulation",
    "tachographs",
]

ALL_TYPES       = "ukpga+uksi"     # UK Acts + UK Statutory Instruments
PRIMARY_TYPES   = "ukpga+asp+nia"  # Acts of Parliament
SECONDARY_TYPES = "uksi"           # Statutory Instruments only

# Layer 1B: Title keyword search
# The API also lets you search by a keyword in the title:
#   GET /title/{title}/data.feed
TITLE_KEYWORDS = [
    "transport act",
    "road traffic",
    "road safety",
    "highways act",
    "motor vehicles",
    "driving licences",
    "traffic signs",
    "traffic regulation",
    "railways act",
    "civil aviation",
    "air traffic",
    "unmanned aircraft",
    "automated vehicles",
    "electric vehicles",
    "taxis",
    "private hire vehicles",
    "goods vehicles",
    "vehicle registration",
    "tachograph",
    "pedicabs",
    "shipping act",
]

# Layer 2: Year enumeration — searches every year for SIs and filters by title
YEAR_ENUM_RANGE = range(2000, 2027)
YEAR_ENUM_TYPES = ["uksi"]
YEAR_FILTER_RE = re.compile(
    r"transport|road\s*traffic|highway|motor\s*vehicle|driving|railway"
    r"|aviation|shipping|tachograph|traffic\s*sign|vehicle\s*licen"
    r"|automated\s*vehicle|electric\s*vehicle|taxis|pedicab",
    re.IGNORECASE,
)


# ─────────────────────────────────────────────
# SEED ACTS — always included regardless of discovery
# ─────────────────────────────────────────────
# These are the core pieces of legislation for our transport law corpus.
# Even if the API search misses them, they'll always be downloaded.
SEED_ACTS = [
    ("ukpga", 2024,  3, "Automated Vehicles Act 2024"),
    ("ukpga", 2024,  2, "Pedicabs (London) Act 2024"),
    ("ukpga", 1988, 52, "Road Traffic Act 1988"),
    ("ukpga", 1988, 53, "Road Traffic Offenders Act 1988"),
    ("ukpga", 1984, 27, "Road Traffic Regulation Act 1984"),
    ("ukpga", 2006, 49, "Road Safety Act 2006"),
    ("ukpga", 2000, 38, "Transport Act 2000"),
    ("ukpga", 1993, 43, "Railways Act 1993"),
    ("ukpga", 2005, 14, "Railways Act 2005"),
    ("ukpga", 2021, 12, "Air Traffic Management and Unmanned Aircraft Act 2021"),
    ("ukpga", 2012, 19, "Civil Aviation Act 2012"),
    ("ukpga", 2018, 18, "Automated and Electric Vehicles Act 2018"),
    ("ukpga", 2022, 14, "Taxis and Private Hire Vehicles Act 2022"),
    ("ukpga", 1980, 34, "Highways Act 1980"),
    ("ukpga", 2023, 32, "Energy Act 2023"),
    ("uksi",  2024, 566, "Goods Vehicles (International Road Transport) Regs 2024"),
    ("uksi",  2024, 615, "Motor Vehicles (Driving Licences) (Amendment) Regs 2024"),
    ("uksi",  2024, 305, "Road Vehicles (Registration and Licensing) Regs 2024"),
    ("uksi",  2023, 980, "Traffic Signs (Amendment) Regulations 2023"),
    ("uksi",  2023, 695, "Drivers' Hours and Tachographs (Amendment) Regs 2023"),
    ("uksi",  2023, 903, "Railways (Access, Management) (Amendment) Regs 2023"),
]


# ═══════════════════════════════════════════════════════════
# URI REGISTRY
# ═══════════════════════════════════════════════════════════
# This class stores all the legislation URIs we discover.
# It automatically deduplicates — if we find the same URI twice,
# it only keeps one copy.

class URIRegistry:
    """Deduplicated store of all discovered legislation URIs."""

    PRIMARY_TYPES   = {"ukpga", "asp", "anaw", "mwa", "nia", "ukcm"}
    SECONDARY_TYPES = {"uksi"}
    NON_UK_SI_TYPES = {"ssi", "wsi", "nisr", "ukmo", "ukmd"}  # Excluded

    def __init__(self):
        self._items = OrderedDict()

    def add(self, item: dict):
        """Add an item. If it already exists (same URI), it's skipped."""
        key = item["uri"]
        if key not in self._items:
            self._items[key] = item

    def add_seed(self, leg_type, year, number, title):
        """Add a seed item (guaranteed to be included)."""
        self.add({
            "uri": f"/{leg_type}/{year}/{number}",
            "title": title,
            "type": leg_type,
            "year": year,
            "number": number,
        })

    @property
    def items(self):
        return list(self._items.values())

    def __len__(self):
        return len(self._items)

    def acts(self):
        """Return only primary Acts (e.g. ukpga)."""
        return [i for i in self.items if i["type"] in self.PRIMARY_TYPES]

    def sis(self):
        """Return only Statutory Instruments (uksi)."""
        return [i for i in self.items if i["type"] in self.SECONDARY_TYPES]

    def filter_by_year(self, min_si_year: int):
        """Remove non-UK SIs entirely + UK SIs older than min_si_year. Keep all primary Acts."""
        before = len(self._items)
        to_remove = [
            key for key, item in self._items.items()
            if (
                # Drop all non-UK SI types (Welsh, Scottish, NI)
                item["type"] in self.NON_UK_SI_TYPES
                # Drop UK SIs older than min year
                or (item["type"] in self.SECONDARY_TYPES and item["year"] < min_si_year)
            )
        ]
        for key in to_remove:
            del self._items[key]
        pruned = len(to_remove)
        log.info(f"  Year filter (uksi >= {min_si_year}, drop non-UK SIs): removed {pruned}, kept {len(self._items)}")
        return pruned


# ATOM FEED PARSER
# The legislation.gov.uk API returns search results as Atom XML feeds.
# Each feed contains a list of <entry> elements with legislation URIs.
# If there are more results, the feed contains a "next" link to the next page.

def parse_atom_feed(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    """
    Parse an Atom feed XML and return:
      - a list of legislation entries (each with uri, title, type, year, number)
      - the URL of the next page (or None if this is the last page)
    """
    entries = []
    next_url = None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return entries, next_url

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # Look for a "next" page link
    for link in root.findall("atom:link", ns):
        if link.get("rel") == "next":
            href = link.get("href", "")
            if href:
                # Make sure we request the feed format
                if not href.endswith("/data.feed") and "data.feed" not in href:
                    href = href.rstrip("/") + "/data.feed"
                next_url = href
            break

    # Extract each entry
    for entry in root.findall("atom:entry", ns):
        id_el = entry.find("atom:id", ns)
        if id_el is None:
            continue
        raw = (id_el.text or "").strip()

        # Convert full URL to path: https://www.legislation.gov.uk/id/ukpga/2024/3 → /ukpga/2024/3
        path = re.sub(r"^https?://www\.legislation\.gov\.uk", "", raw)
        path = re.sub(r"^/id/", "/", path)  # Strip the /id/ prefix

        # Must match the pattern /{type}/{year}/{number}
        m = re.match(r"^/([a-z]+)/(\d+)/(\d+)$", path)
        if not m:
            continue

        leg_type = m.group(1)
        year = int(m.group(2))
        number = int(m.group(3))

        title_el = entry.find("atom:title", ns)
        title = (title_el.text or "").strip() if title_el is not None else ""

        entries.append({
            "uri":    path,
            "title":  title,
            "type":   leg_type,
            "year":   year,
            "number": number,
        })

    return entries, next_url


# HTTP HELPERS

def build_session() -> requests.Session:
    """
    Build a requests Session with automatic retry logic.
    If the server returns 429 (rate limit) or 5xx (server error),
    the session will automatically retry with increasing wait times.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=2,
        status_forcelist=[429, 436, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


def get_bytes(session: requests.Session, url: str) -> bytes | None:
    """
    Download a URL and return its content as bytes.
    Waits a short time before each request to avoid hitting rate limits.
    Returns None if the request fails.
    """
    time.sleep(DOWNLOAD_RATE_LIMIT_WAIT)
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
        if r.status_code == 200:
            return r.content
        if r.status_code == 436:
            r.raise_for_status()
        if r.status_code not in (404, 410):
            log.warning(f"HTTP {r.status_code} for {url}")
        return None
    except requests.RequestException as e:
        log.warning(f"Request failed ({type(e).__name__}): {url}")
        return None


def save_file(data: bytes, filepath: str) -> bool:
    """Save raw bytes to a file. Returns True on success."""
    try:
        with open(filepath, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


# DISCOVERY FUNCTIONS
# These functions search the legislation.gov.uk API to find
# all transport-related legislation URIs.

def exhaust_feed(session: requests.Session, initial_url: str, registry: URIRegistry, label: str = "") -> int:
    """
    Follow an Atom feed page by page until there are no more results.
    Adds all discovered entries to the registry.
    Returns the count of new items added.
    """
    url = initial_url
    added = 0
    page = 0

    while url and page < MAX_PAGES_PER_FEED:
        data = get_bytes(session, url)
        if not data:
            break

        entries, next_url = parse_atom_feed(data)
        before = len(registry)
        for e in entries:
            registry.add(e)
        added += len(registry) - before

        url = next_url
        page += 1

    if label:
        log.info(f"  {label}: +{added} new  (pages={page})")
    return added


def discover_by_subject(session: requests.Session, registry: URIRegistry):
    """
    Layer 1A — Search by subject path.
    For each transport subject (e.g. "road-traffic"), query the API
    and add all results to the registry.
    """
    log.info("── Layer 1A: Subject path search")
    before = len(registry)

    for subject in TRANSPORT_SUBJECTS:
        url = f"{BASE_URL}/{ALL_TYPES}/{subject}/data.feed"
        exhaust_feed(session, url, registry, label=f"subject/{subject}")

    log.info(f"  Layer 1A TOTAL: +{len(registry) - before} new  (registry={len(registry)})")


def discover_by_title(session: requests.Session, registry: URIRegistry):
    """
    Layer 1B — Search by title keyword.
    For each keyword (e.g. "road traffic"), query the API
    and add all matching results to the registry.
    """
    log.info("── Layer 1B: Title keyword search")
    before = len(registry)

    for kw in TITLE_KEYWORDS:
        encoded = quote(kw)
        url = f"{BASE_URL}/title/{encoded}/data.feed"
        exhaust_feed(session, url, registry, label=f"title/{kw}")

    log.info(f"  Layer 1B TOTAL: +{len(registry) - before} new  (registry={len(registry)})")


def discover_by_year_enum(session: requests.Session, registry: URIRegistry):
    """
    Layer 2 — Year enumeration.
    For each year and SI type, list ALL legislation in that year,
    then filter client-side to only keep transport-related items.
    """
    log.info("── Layer 2: Year enumeration (exhaustive)")
    before = len(registry)

    for leg_type in YEAR_ENUM_TYPES:
        for year in YEAR_ENUM_RANGE:
            url = f"{BASE_URL}/{leg_type}/{year}/data.feed"
            added = 0
            page = 0

            while url and page < MAX_PAGES_PER_FEED:
                data = get_bytes(session, url)
                if not data:
                    break

                entries, next_url = parse_atom_feed(data)
                for entry in entries:
                    # Only keep entries whose title matches transport keywords
                    if YEAR_FILTER_RE.search(entry.get("title", "")):
                        before_add = len(registry)
                        registry.add(entry)
                        added += len(registry) - before_add

                url = next_url
                page += 1

            if added:
                log.info(f"    {leg_type}/{year}: +{added}")

    log.info(f"  Layer 2 TOTAL: +{len(registry) - before} new  (registry={len(registry)})")


# DOWNLOAD FUNCTIONS

def download_legislation(session: requests.Session, registry: URIRegistry) -> int:
    """
    Download the primary XML for every item in the registry.
    Acts go into raw_legislation/, SIs go into raw_statutory_instruments/.
    Skips files that already exist on disk.
    """
    log.info(f"── Downloading legislation XML  ({len(registry)} items)")
    ok = 0
    skip = 0
    fail = 0

    for item in registry.items:
        leg_type = item["type"]
        year = item["year"]
        number = item["number"]

        # Choose output folder based on type
        if leg_type in URIRegistry.PRIMARY_TYPES:
            out_dir = RAW_LEGISLATION_DIR
        else:
            out_dir = RAW_SI_DIR

        filename = f"{leg_type}_{year}_{number}.xml"
        filepath = os.path.join(out_dir, filename)

        # Skip if already downloaded
        if os.path.exists(filepath):
            skip += 1
            continue

        # Download the XML
        url = f"{BASE_URL}/{leg_type}/{year}/{number}/data.xml"
        data = get_bytes(session, url)

        if data and data.strip().startswith(b"<"):
            save_file(data, filepath)
            ok += 1
            log.debug(f"  saved {filename}")
        else:
            fail += 1
            if data:
                log.warning(f"  unexpected content for {url}: {data[:60]}")

    log.info(f"  Legislation: OK={ok}  SKIP={skip}  FAIL={fail}")
    return ok


def download_effects(session: requests.Session, registry: URIRegistry) -> int:
    """
    Download the Effects/Changes feed for each item.
    The effects feed tells us which other legislation amends this one.
    URL pattern: GET /changes/affected/{type}/{year}/{number}/data.feed
    """
    log.info(f"── Downloading legislative effects  ({len(registry)} items)")
    ok = 0
    skip = 0
    fail = 0

    for item in registry.items:
        leg_type = item["type"]
        year = item["year"]
        number = item["number"]

        filename = f"{leg_type}_{year}_{number}_effects.xml"
        filepath = os.path.join(AMENDMENTS_DIR, filename)

        # Skip if already downloaded
        if os.path.exists(filepath):
            skip += 1
            continue

        # Download the effects feed
        url = f"{BASE_URL}/changes/affected/{leg_type}/{year}/{number}/data.feed"
        data = get_bytes(session, url)

        if data and data.strip().startswith(b"<"):
            save_file(data, filepath)
            ok += 1
        else:
            fail += 1

    log.info(f"  Effects: OK={ok}  SKIP={skip}  FAIL={fail}")
    return ok


# MANIFEST


def save_manifest(registry: URIRegistry, stats: dict, elapsed: float):
    """Save a JSON manifest recording what was downloaded and when."""
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
        "stats":        stats,
        "total_uris":   len(registry),
        "acts_count":   len(registry.acts()),
        "sis_count":    len(registry.sis()),
        "items": [
            {"uri": i["uri"], "title": i["title"],
             "type": i["type"], "year": i["year"], "number": i["number"]}
            for i in registry.items
        ],
    }
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)
    log.info(f"  Manifest → {MANIFEST_FILE}")


# MAIN

def run():
    t0 = time.time()
    stats = {}
    registry = URIRegistry()

    # ── Step 0: Load seed items ──────────────────────────
    log.info(f"Loading {len(SEED_ACTS)} seed items...")
    for leg_type, year, number, title in SEED_ACTS:
        registry.add_seed(leg_type, year, number, title)

    # ── Step 1: Connectivity self-test ───────────────────
    log.info("Running connectivity self-test...")
    try:
        r = requests.get(
            f"{BASE_URL}/ukpga/1988/52/data.xml",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200 and r.content.strip().startswith(b"<"):
            log.info(f"  ✅ Connected to legislation.gov.uk ({len(r.content):,} bytes)")
        else:
            log.error(f"  ❌ CANNOT REACH legislation.gov.uk (HTTP {r.status_code})")
            log.error("     Check network / VPN / Colab runtime. Aborting.")
            return
    except requests.RequestException as e:
        log.error(f"  ❌ CANNOT REACH legislation.gov.uk: {e}")
        return

    # ── Step 2: Discover legislation URIs ────────────────
    with build_session() as session:

        discover_by_subject(session, registry)
        discover_by_title(session, registry)
        discover_by_year_enum(session, registry)

        log.info(f"""
{'='*55}
DISCOVERY COMPLETE (before filter)
  Total unique URIs : {len(registry)}
  Primary Acts      : {len(registry.acts())}
  SIs / instruments : {len(registry.sis())}
{'='*55}""")

        # ── Step 3: Filter old SIs ───────────────────────
        registry.filter_by_year(MIN_SI_YEAR)
        log.info(f"  After filter: {len(registry)} URIs  "
                 f"(Acts={len(registry.acts())}, SIs={len(registry.sis())})")

        # ── Step 4: Download XML files ───────────────────
        stats["legislation"] = download_legislation(session, registry)
        stats["effects"]     = download_effects(session, registry)

        # Case law is handled by script 3 (3_download_caselaw.py)
        stats["cases"] = 0

    # ── Step 5: Save manifest ────────────────────────────
    elapsed = time.time() - t0
    save_manifest(registry, stats, elapsed)

    print(f"""
{'='*55}
ALL DONE  ({elapsed:.0f}s  ≈  {elapsed/60:.1f} min)
{'='*55}
  Legislation downloaded : {stats.get('legislation', 0)}
  Effects feeds          : {stats.get('effects', 0)}
  Case law               : {stats.get('cases', 0)}
  Output dir             : data/
  Manifest               : {MANIFEST_FILE}
""")


if __name__ == "__main__":
    print("  Running Legislation Download (Full Exhaustive Crawl)")
    run()
