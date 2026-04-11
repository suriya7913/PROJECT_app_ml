#!/usr/bin/env python3
"""
LegalKGent — Step 1B: Smart Case Law Downloader
================================================
Reads the processed legislation corpus (from 2_build_corpus_legislation.py)
and autonomously generates highly specific search queries to find relevant
judgments from the National Archives.

Queries are formatted as exact-match combinations:
    "Transport Act 1980" AND "section 34"

Usage:
    python 3_download_caselaw.py
"""

import os, json, time, asyncio, logging
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import xml.etree.ElementTree as ET

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
CORPUS_FILE = "data/legal_corpus_final.json"
CASELAW_DIR = "data/raw_caselaw"
BASE_ATOM_URL = "https://caselaw.nationalarchives.gov.uk/atom.xml"
HEADERS = {
    "User-Agent": "LegalKGent-Research-Project/3.0 (university-research)",
    "Accept": "application/xml, application/atom+xml, */*",
}
MAX_CONCURRENT = 4  # National Archives rate limit is usually accommodating, keep reasonable
MAX_RETRIES = 3

os.makedirs(CASELAW_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("SmartCaselaw")

# ─────────────────────────────────────────────
# HTTP CLIENT (Reused from 1_download_data)
# ─────────────────────────────────────────────
class AsyncClient:
    def __init__(self, workers: int = MAX_CONCURRENT):
        self._workers = workers
        self._executor = None
        self._session = None
        self._loop = None

    async def __aenter__(self):
        self._executor = ThreadPoolExecutor(max_workers=self._workers)
        session = requests.Session()
        session.headers.update(HEADERS)
        retry = Retry(total=MAX_RETRIES, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=self._workers, pool_maxsize=self._workers)
        session.mount("https://", adapter)
        self._session = session
        self._loop = asyncio.get_event_loop()
        return self

    async def __aexit__(self, *_):
        self._executor.shutdown(wait=False)
        self._session.close()

    def _sync_get(self, url: str) -> bytes | None:
        try:
            r = self._session.get(url, timeout=20)
            if r.status_code == 200:
                return r.content
        except requests.RequestException as e:
            log.warning(f"Request failed: {url} - {e}")
        return None

    async def get_bytes(self, url: str) -> bytes | None:
        return await self._loop.run_in_executor(self._executor, self._sync_get, url)

# ─────────────────────────────────────────────
# PARSING LOGIC
# ─────────────────────────────────────────────
def generate_queries() -> set[str]:
    """Parse existing data to generate strict exact-match queries."""
    if not os.path.exists(CORPUS_FILE):
        log.error(f"Missing {CORPUS_FILE}. Run 2_build_corpus_legislation.py first!")
        return set()
    
    with open(CORPUS_FILE, 'r', encoding='utf-8') as f:
        corpus = json.load(f)

    queries = set()
    for chunk in corpus:
        source = chunk.get("source", "")
        # Fallback check if source is missing but it's legislation
        is_leg = source == "legislation" or "ukpga" in str(chunk.get("chunk_id", ""))
        if is_leg:
            title = chunk.get("doc_title")
            if title:
                # Use strict quotes on Act title. Avoid "section X" as it breaks API recall.
                queries.add(f'"{title}"')

    log.info(f"Generated {len(queries)} unique exact-match queries from corpus.")
    return queries

async def extract_caselaw_links(client: AsyncClient, query_string: str) -> list[str]:
    """Search the Atom feed and return direct XML document URLs."""
    encoded_query = quote(query_string)
    url = f"{BASE_ATOM_URL}?query={encoded_query}"
    
    data = await client.get_bytes(url)
    if not data:
        return []

    links = []
    try:
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            # The direct XML document is usually linked with type="application/akn+xml"
            for link in entry.findall("atom:link", ns):
                if link.get("type", "") == "application/akn+xml":
                    href = link.get("href")
                    if href:
                        links.append(href)
    except Exception as e:
        log.warning(f"Failed to parse Atom feed for {query_string}: {e}")
    
    return links

async def process_queries(client: AsyncClient, queries: set[str]):
    """Run searches and compile all unique judgment XML links."""
    queries_list = sorted(list(queries))

    all_links = set()
    
    async def run_search(q: str):
        links = await extract_caselaw_links(client, q)
        if links:
            log.info(f"Query [{q}] -> Found {len(links)} judgments")
            all_links.update(links)

    log.info("Searching National Archives...")
    # Run in batches
    for i in range(0, len(queries_list), MAX_CONCURRENT):
        batch = queries_list[i:i + MAX_CONCURRENT]
        await asyncio.gather(*[run_search(q) for q in batch])
        time.sleep(1) # Polite pause

    return list(all_links)

async def download_judgments(client: AsyncClient, links: list[str]):
    """Download the actual AKN XML documents."""
    log.info(f"Downloading {len(links)} unique case law XML documents...")
    success = 0
    
    async def dl(url: str):
        nonlocal success
        # URL usually looks like https://caselaw.nationalarchives.gov.uk/uksc/2024/1/data.xml
        parts = url.strip('/').split('/')
        # Attempt to synthesize a clean filename: uksc_2024_1.xml
        try:
            # Drop domain and /data.xml
            idx = parts.index("caselaw.nationalarchives.gov.uk")
            core_parts = parts[idx+1:-1]
            filename = "_".join(core_parts) + ".xml"
        except:
            filename = f"judgment_{hash(url)}.xml"
            
        filepath = os.path.join(CASELAW_DIR, filename)
        if os.path.exists(filepath):
            success += 1
            return
            
        data = await client.get_bytes(url)
        if data and data.strip().startswith(b"<"):
            with open(filepath, "wb") as f:
                f.write(data)
            success += 1

    for i in range(0, len(links), MAX_CONCURRENT):
        batch = links[i:i + MAX_CONCURRENT]
        await asyncio.gather(*[dl(l) for l in batch])
        
    log.info(f"Completed! Downloaded {success}/{len(links)} judgments to {CASELAW_DIR}/")

async def run():
    queries = generate_queries()
    if not queries:
        return
        
    t0 = time.time()
    async with AsyncClient() as client:
        links = await process_queries(client, queries)
        if links:
            await download_judgments(client, links)
        else:
            log.info("No judgments found for generated queries.")
            
    log.info(f"Smart download finished in {time.time() - t0:.1f}s")

if __name__ == "__main__":
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except:
        pass
    asyncio.run(run())
