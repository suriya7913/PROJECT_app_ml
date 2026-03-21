#!/usr/bin/env python3
"""
LegalKGent — Transportation Law Data Downloader v3
====================================================
Rewrote using the OFFICIAL OpenAPI documentation from legislation.gov.uk.

Key corrections vs v2:
  ✅ Subject search uses PATH segments (not query params):
       /ukpga+uksi/transport/data.feed
       /{type}/{year}/{subject}/data.feed
  ✅ Title search uses /title/{title}/data.feed
  ✅ Effects/Changes API uses /changes/affected/{type}/{year}/{num}/data.feed
  ✅ Notes uses /{type}/{year}/{num}/notes/data.xml (notesType path segment)
  ✅ Full parallel I/O via requests + ThreadPoolExecutor
       → 3000 req/5min rate limit = 10/s max; 8 concurrent is safe
       → Expected speedup: ~8x vs v2 sequential

Estimated runtime (Colab):
  Full run  : ~20-30 min  (was ~2.5 hrs in v2
  Fast mode : ~5-8 min    (--fast: skip year enum + limit pages)

Usage:
    python download_transport_data_v3.py
    python download_transport_data_v3.py --fast           # Quick run, fewer pages
    python download_transport_data_v3.py --discover-only  # Just list URIs found
    python download_transport_data_v3.py --skip-year-enum # Skip slow year crawl
    python download_transport_data_v3.py --workers 12     # More workers (careful)
"""

import os, re, json, time, logging, asyncio
from datetime import datetime
from collections import OrderedDict
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ASYNC_AVAILABLE = True  # kept for compatibility — we use threads not aiohttp

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("LegalKGent")

# ─────────────────────────────────────────────
# DIRECTORIES
# ─────────────────────────────────────────────
OUTPUT_DIR      = "data"
LEGISLATION_DIR = os.path.join(OUTPUT_DIR, "raw_legislation")
CASELAW_DIR     = os.path.join(OUTPUT_DIR, "raw_caselaw")
SI_DIR          = os.path.join(OUTPUT_DIR, "raw_statutory_instruments")
AMENDMENTS_DIR  = os.path.join(OUTPUT_DIR, "amendments")
NOTES_DIR       = os.path.join(OUTPUT_DIR, "explanatory_notes")
MANIFEST_FILE   = os.path.join(OUTPUT_DIR, "download_manifest.json")

for d in [LEGISLATION_DIR, CASELAW_DIR, SI_DIR, AMENDMENTS_DIR]:
    os.makedirs(d, exist_ok=True)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
BASE_URL       = "https://www.legislation.gov.uk"
HEADERS        = {
    "User-Agent": "LegalKGent-Research-Project/3.0 (university-research)",
    "Accept":     "application/xml, application/atom+xml, */*",
}
MAX_CONCURRENT = 2     # safe under 10 req/s rate limit
MAX_RETRIES    = 3
RETRY_DELAY    = 5.0
MAX_PAGES      = 300    # safety cap per feed
DEFAULT_PAGE_SIZE = 20  # legislation.gov.uk default

# ─────────────────────────────────────────────
# DISCOVERY CONFIG — using CORRECT API paths from official docs
# ─────────────────────────────────────────────

# Layer 1A: Subject path search — confirmed from OpenAPI:
#   GET /{type}/{subject}/data.feed
#   GET /{type}/{year}/{subject}/data.feed
# Subject slugs are hyphenated lowercase terms used by legislation.gov.uk
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

ALL_TYPES       = "ukpga+uksi+asp+wsi+ssi+nisr"
PRIMARY_TYPES   = "ukpga+asp+nia"
SECONDARY_TYPES = "uksi+wsi+ssi+nisr"

# Layer 1B: Title keyword search — confirmed from OpenAPI:
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

# Layer 2: Year enumeration — GET /{type}/{year}/data.feed
YEAR_ENUM_RANGE = range(1988, 2025)   # focused range for transport
YEAR_ENUM_TYPES = ["uksi", "wsi", "ssi", "nisr"]
YEAR_FILTER_RE  = re.compile(
    r"transport|road\s*traffic|highway|motor\s*vehicle|driving|railway"
    r"|aviation|shipping|tachograph|traffic\s*sign|vehicle\s*licen"
    r"|automated\s*vehicle|electric\s*vehicle|taxis|pedicab",
    re.IGNORECASE,
)

# ─────────────────────────────────────────────
# SEED FALLBACK — always included regardless of discovery
# ─────────────────────────────────────────────
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
class URIRegistry:
    """Deduplicated store of all discovered legislation URIs."""

    def __init__(self):
        self._items = OrderedDict()

    def add(self, item: dict):
        key = item["uri"]
        if key not in self._items:
            self._items[key] = item

    def add_seed(self, leg_type, year, number, title):
        self.add({"uri": f"/{leg_type}/{year}/{number}",
                  "title": title, "type": leg_type,
                  "year": year, "number": number})

    @property
    def items(self):
        return list(self._items.values())

    def __len__(self):
        return len(self._items)

    def acts(self):
        primary = {"ukpga", "asp", "anaw", "mwa", "nia", "ukcm"}
        return [i for i in self.items if i["type"] in primary]

    def sis(self):
        secondary = {"uksi", "ssi", "wsi", "nisr", "ukmo", "ukmd"}
        return [i for i in self.items if i["type"] in secondary]


# ═══════════════════════════════════════════════════════════
# ATOM FEED PARSER
# ═══════════════════════════════════════════════════════════
import xml.etree.ElementTree as ET

def parse_atom_feed(xml_bytes: bytes) -> tuple[list[dict], str | None]:
    """
    Returns (entries_list, next_url_or_None).
    Handles the Atom feed format returned by legislation.gov.uk.
    """
    entries = []
    next_url = None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return entries, next_url

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
    }

    # Next page link
    for link in root.findall("atom:link", ns):
        if link.get("rel") == "next":
            href = link.get("href", "")
            if href:
                # Ensure we get the feed format
                if not href.endswith("/data.feed") and "data.feed" not in href:
                    href = href.rstrip("/") + "/data.feed"
                next_url = href
            break

    # Entries
    for entry in root.findall("atom:entry", ns):
        id_el = entry.find("atom:id", ns)
        if id_el is None:
            continue
        raw = (id_el.text or "").strip()

        # Normalise to path
        path = re.sub(r"^https?://www\.legislation\.gov\.uk", "", raw)

        # Must match /{type}/{year}/{number}
        m = re.match(r"^/([a-z]+)/(\d+)/(\d+)$", path)
        if not m:
            continue

        leg_type, year, number = m.group(1), int(m.group(2)), int(m.group(3))
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


# ═══════════════════════════════════════════════════════════
# HTTP CLIENT — requests + ThreadPoolExecutor
# Uses requests (proven to work through Cloudflare on Colab)
# with a thread pool for parallelism.  aiohttp was replaced
# because its TLS fingerprint triggers Cloudflare's 437 block
# while requests does not.
# ═══════════════════════════════════════════════════════════
class AsyncClient:
    """
    Drop-in replacement for the aiohttp client.
    Uses requests.Session in a ThreadPoolExecutor.
    All public methods are async-compatible via asyncio.run_in_executor.
    """

    def __init__(self, workers: int = MAX_CONCURRENT):
        self._workers = workers
        self._executor: ThreadPoolExecutor | None = None
        self._session:  requests.Session | None = None
        self._loop = None

    async def __aenter__(self):
        self._executor = ThreadPoolExecutor(max_workers=self._workers)
        self._session  = self._make_session()
        self._loop     = asyncio.get_event_loop()
        return self

    async def __aexit__(self, *_):
        if self._executor:
            self._executor.shutdown(wait=False)
        if self._session:
            self._session.close()

    @staticmethod
    def _make_session() -> requests.Session:
        """Build a requests.Session with retry logic and connection pooling."""
        session = requests.Session()
        session.headers.update(HEADERS)
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=2,
            status_forcelist=[429, 436, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=MAX_CONCURRENT + 4,
            pool_maxsize=MAX_CONCURRENT + 4,
        )
        session.mount("https://", adapter)
        session.mount("http://",  adapter)
        return session

    def _sync_get(self, url: str) -> bytes | None:
        """Synchronous GET — runs inside thread pool."""
        time.sleep(0.4)
        try:
            r = self._session.get(url, timeout=30, allow_redirects=True)
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

    async def get_bytes(self, url: str) -> bytes | None:
        """Async wrapper — offloads HTTP call to thread pool."""
        return await self._loop.run_in_executor(
            self._executor, self._sync_get, url
        )


# ═══════════════════════════════════════════════════════════
# DISCOVERY FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def _exhaust_feed(client: AsyncClient, initial_url: str,
                        registry: URIRegistry, label: str = "",
                        max_pages: int = MAX_PAGES) -> int:
    """Follow an Atom feed until exhausted. Returns count of new items added."""
    url = initial_url
    added = 0
    page  = 0

    while url and page < max_pages:
        data = await client.get_bytes(url)
        if not data:
            break

        entries, next_url = parse_atom_feed(data)
        before = len(registry)
        for e in entries:
            registry.add(e)
        added += len(registry) - before

        url   = next_url
        page += 1

    if label:
        log.info(f"  {label}: +{added} new  (pages={page})")
    return added


async def discover_by_subject(client: AsyncClient, registry: URIRegistry,
                               fast: bool = False):
    """
    Layer 1A — Subject path search (CONFIRMED from OpenAPI docs).
    Pattern: GET /{type}/{subject}/data.feed
    """
    log.info("── Layer 1A: Subject path search")
    before = len(registry)
    max_pages = 10 if fast else MAX_PAGES

    tasks = []
    for subject in TRANSPORT_SUBJECTS:
        url = f"{BASE_URL}/{ALL_TYPES}/{subject}/data.feed"
        tasks.append(_exhaust_feed(client, url, registry,
                                   label=f"subject/{subject}", max_pages=max_pages))

    await asyncio.gather(*tasks)
    log.info(f"  Layer 1A TOTAL: +{len(registry) - before} new  (registry={len(registry)})")


async def discover_by_title(client: AsyncClient, registry: URIRegistry,
                             fast: bool = False):
    """
    Layer 1B — Title search (CONFIRMED from OpenAPI docs).
    Pattern: GET /title/{title}/data.feed
    """
    log.info("── Layer 1B: Title keyword search")
    before = len(registry)
    max_pages = 5 if fast else MAX_PAGES

    tasks = []
    for kw in TITLE_KEYWORDS:
        encoded = quote(kw)
        url = f"{BASE_URL}/title/{encoded}/data.feed"
        tasks.append(_exhaust_feed(client, url, registry,
                                   label=f"title/{kw}", max_pages=max_pages))

    await asyncio.gather(*tasks)
    log.info(f"  Layer 1B TOTAL: +{len(registry) - before} new  (registry={len(registry)})")


async def discover_by_year_enum(client: AsyncClient, registry: URIRegistry,
                                 skip: bool = False, fast: bool = False):
    """
    Layer 2 — Year enumeration.
    Pattern: GET /{type}/{year}/data.feed  (filter client-side by title)
    """
    if skip:
        log.info("  ⏭  Layer 2 (year enum) skipped")
        return

    log.info("── Layer 2: Year enumeration (exhaustive)")
    before    = len(registry)
    max_pages = 3 if fast else 20  # uksi years have ~50+ items/page * 20 pages = 1000

    # Build all (type, year) combos as tasks
    async def _year_task(leg_type: str, year: int):
        url = f"{BASE_URL}/{leg_type}/{year}/data.feed"
        added = 0
        page  = 0

        while url and page < max_pages:
            data = await client.get_bytes(url)
            if not data:
                break
            entries, next_url = parse_atom_feed(data)
            for e in entries:
                if YEAR_FILTER_RE.search(e.get("title", "")):
                    before_add = len(registry)
                    registry.add(e)
                    added += len(registry) - before_add
            url   = next_url
            page += 1

        return leg_type, year, added

    tasks = [
        _year_task(t, y)
        for t in YEAR_ENUM_TYPES
        for y in YEAR_ENUM_RANGE
    ]

    results = await asyncio.gather(*tasks)

    for leg_type, year, added in results:
        if added:
            log.info(f"    {leg_type}/{year}: +{added}")

    log.info(f"  Layer 2 TOTAL: +{len(registry) - before} new  (registry={len(registry)})")


# ═══════════════════════════════════════════════════════════
# DOWNLOAD FUNCTIONS
# ═══════════════════════════════════════════════════════════

async def _save(data: bytes, filepath: str):
    """Write bytes to file (sync write is fine — fast disk op)."""
    try:
        with open(filepath, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


async def download_legislation(client: AsyncClient, registry: URIRegistry):
    """Download primary XML for every item in the registry."""
    log.info(f"── Downloading legislation XML  ({len(registry)} items)")
    ok = skip = fail = 0

    async def _dl(item: dict):
        nonlocal ok, skip, fail
        leg_type, year, number = item["type"], item["year"], item["number"]
        out_dir  = LEGISLATION_DIR if leg_type == "ukpga" else SI_DIR
        filename = f"{leg_type}_{year}_{number}.xml"
        filepath = os.path.join(out_dir, filename)

        if os.path.exists(filepath):
            skip += 1
            return

        url  = f"{BASE_URL}/{leg_type}/{year}/{number}/data.xml"
        data = await client.get_bytes(url)

        if data and data.strip().startswith(b"<"):
            await _save(data, filepath)
            ok += 1
            log.debug(f"  saved {filename}")
        else:
            fail += 1
            if data:
                log.warning(f"  unexpected content for {url}: {data[:60]}")

    await asyncio.gather(*[_dl(i) for i in registry.items])
    log.info(f"  Legislation: OK={ok}  SKIP={skip}  FAIL={fail}")
    return ok


async def download_effects(client: AsyncClient, registry: URIRegistry):
    """
    Download the Effects/Changes feed for each item.
    CORRECT URL from official Effects API docs:
      GET /changes/affected/{affectedType}/{affectedYear}/{affectedNumber}/data.feed
    (NOT the old /{type}/{year}/{num}/changes/affected/data.feed)
    """
    log.info(f"── Downloading legislative effects  ({len(registry)} items)")
    ok = skip = fail = 0

    async def _dl(item: dict):
        nonlocal ok, skip, fail
        leg_type, year, number = item["type"], item["year"], item["number"]
        filename = f"{leg_type}_{year}_{number}_effects.xml"
        filepath = os.path.join(AMENDMENTS_DIR, filename)

        if os.path.exists(filepath):
            skip += 1
            return

        # Confirmed URL from Legislative Effects OpenAPI docs
        url  = f"{BASE_URL}/changes/affected/{leg_type}/{year}/{number}/data.feed"
        data = await client.get_bytes(url)

        if data and data.strip().startswith(b"<"):
            await _save(data, filepath)
            ok += 1
        else:
            fail += 1

    await asyncio.gather(*[_dl(i) for i in registry.items])
    log.info(f"  Effects: OK={ok}  SKIP={skip}  FAIL={fail}")
    return ok





async def download_case_law(client: AsyncClient,
                             courts=None, years=None, max_per_court=10):
    """Download case law from National Archives."""
    if courts is None:
        courts = ["ewhc/admin", "ewca/civ", "uksc"]
    if years is None:
        years = [2022, 2023, 2024]

    log.info("── Downloading case law")
    success = 0

    async def _try(court, year, num):
        nonlocal success
        filename = f"{court.replace('/', '_')}_{year}_{num}.xml"
        filepath = os.path.join(CASELAW_DIR, filename)

        if os.path.exists(filepath):
            success += 1
            return True

        for url in [
            f"https://caselaw.nationalarchives.gov.uk/{court}/{year}/{num}/data.xml",
            f"https://caselaw.nationalarchives.gov.uk/{court}/{year}/{num}.xml",
        ]:
            data = await client.get_bytes(url)
            if data and data.strip().startswith(b"<"):
                await _save(data, filepath)
                success += 1
                return True
        return False

    for court in courts:
        for year in years:
            results = await asyncio.gather(*[_try(court, year, n)
                                             for n in range(1, max_per_court + 1)])
            hits = sum(results)
            log.info(f"  {court}/{year}: {hits} cases")

    log.info(f"  Case law total: {success}")
    return success


# ═══════════════════════════════════════════════════════════
# MANIFEST
# ═══════════════════════════════════════════════════════════

def save_manifest(registry: URIRegistry, stats: dict, elapsed: float):
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


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

async def run(args):
    print("""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent v3 — Async Parallel Downloader              ║
║  API: Official OpenAPI docs from legislation.gov.uk     ║
║  Concurrent workers: 8  (rate limit: 3000 req/5min)     ║
╚══════════════════════════════════════════════════════════╝
    """)

    t0       = time.time()
    stats    = {}
    registry = URIRegistry()

    # Always load seeds first
    log.info(f"Loading {len(SEED_ACTS)} seed items...")
    for leg_type, year, number, title in SEED_ACTS:
        registry.add_seed(leg_type, year, number, title)

    # ── Connectivity self-test ─────────────────────────────
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

    async with AsyncClient(workers=args.workers) as client:

        # ── Discovery ──────────────────────────────────────
        await discover_by_subject(client, registry, fast=args.fast)
        await discover_by_title(client, registry, fast=args.fast)
        await discover_by_year_enum(client, registry,
                                     skip=args.skip_year_enum,
                                     fast=args.fast)

        log.info(f"""
{'='*55}
DISCOVERY COMPLETE
  Total unique URIs : {len(registry)}
  Primary Acts      : {len(registry.acts())}
  SIs / instruments : {len(registry.sis())}
{'='*55}""")

        if args.discover_only:
            print("\n── URI List ──")
            for item in registry.items:
                print(f"  {item['uri']:40s}  {item['title']}")
            return

        # ── Downloads ──────────────────────────────────────
        stats["legislation"] = await download_legislation(client, registry)
        stats["effects"]     = await download_effects(client, registry)
        


        if getattr(args, 'no_caselaw', True):
            stats["cases"] = 0
        else:
            stats["cases"] = await download_case_law(client)

    elapsed = time.time() - t0
    save_manifest(registry, stats, elapsed)

    print(f"""
{'='*55}
ALL DONE  ({elapsed:.0f}s  ≈  {elapsed/60:.1f} min)
{'='*55}
  Legislation downloaded : {stats.get('legislation', 0)}
  Effects feeds          : {stats.get('effects', 0)}
  Case law               : {stats.get('cases', 0)}
  Case law               : {stats.get('cases', 0)}
  Output dir             : {OUTPUT_DIR}/
  Manifest               : {MANIFEST_FILE}
""")


def main():
    # ╔══════════════════════════════════════════════════════╗
    # ║           COLAB CONFIG — EDIT THIS BLOCK            ║
    # ╠══════════════════════════════════════════════════════╣
    # ║  MODE        │ TIME    │ WHAT IT DOES               ║
    # ║  ─────────── │ ─────── │ ─────────────────────────  ║
    # ║  "fast"      │ ~10 min │ Caps pages per feed.       ║
    # ║              │         │ Good for testing/dev.      ║
    # ║  "full"      │ ~25 min │ Full crawl, all pages.     ║
    # ║              │         │ Use for production.        ║
    # ║  "discover"  │ ~3 min  │ Lists all found URIs,      ║
    # ║              │         │ no downloads at all.       ║
    # ╚══════════════════════════════════════════════════════╝

    MODE = "full"            # ← CHANGE THIS:  "fast" | "full" | "discover"

    # ── Optional fine-tuning ───────────────────────────────
    SKIP_YEAR_ENUM = False   # True = skip year enumeration (Layer 2), saves ~1.5 min
    SKIP_CASELAW   = True    # Skipped for Phase 1
    SKIP_CASELAW   = True    # Skipped for Phase 1
    WORKERS        = 8       # Concurrent requests. Max safe = 10 (rate limit)
    # ──────────────────────────────────────────────────────

    # Build args from config (works in both Colab and terminal)
    class Args:
        fast           = (MODE == "fast")
        discover_only  = (MODE == "discover")
        skip_year_enum = SKIP_YEAR_ENUM
        no_caselaw     = SKIP_CASELAW

        workers        = WORKERS

    args = Args()

    # Colab / Jupyter need nest_asyncio to run asyncio.run() inside the notebook loop
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass

    print(f"  ▶  Mode: {MODE.upper()}  |  Workers: {WORKERS}  |"
          f"  year_enum={'OFF' if SKIP_YEAR_ENUM else 'ON'}  |"
          f"  caselaw={'OFF' if SKIP_CASELAW else 'ON'}")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()