#!/usr/bin/env python3
"""
LegalKGent — Step 1B: Smart Case Law Downloader
================================================
Reads the processed legislation corpus (from 2_build_corpus_legislation.py)
and generates search queries to find relevant judgments from the
National Archives Case Law database.

Queries are formatted as exact-match combinations:
    "Transport Act 1980"

Usage:
    python 3_download_caselaw.py
"""

import os
import json
import time
import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
CORPUS_FILE   = "data/legal_corpus_final.json"
CASELAW_DIR   = "data/raw_caselaw"
BASE_ATOM_URL = "https://caselaw.nationalarchives.gov.uk/atom.xml"
HEADERS = {
    "User-Agent": "LegalKGent-Research-Project/3.0 (university-research)",
    "Accept":     "application/xml, application/atom+xml, */*",
}
MAX_RETRIES = 3
RATE_LIMIT_WAIT = 0.5  # seconds to wait between requests

os.makedirs(CASELAW_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("SmartCaselaw")


# ─────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────

def build_session() -> requests.Session:
    """Build a requests Session with automatic retry on server errors."""
    session = requests.Session()
    session.headers.update(HEADERS)
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session


def get_bytes(session: requests.Session, url: str) -> bytes | None:
    """
    Download a URL and return its content as bytes.
    Waits a short time before each request to be polite to the server.
    Returns None if the request fails.
    """
    time.sleep(RATE_LIMIT_WAIT)
    try:
        r = session.get(url, timeout=20, allow_redirects=True)
        if r.status_code == 200:
            return r.content
        if r.status_code not in (404, 410):
            log.warning(f"HTTP {r.status_code} for {url}")
        return None
    except requests.RequestException as e:
        log.warning(f"Request failed ({type(e).__name__}): {url}")
        return None


# ─────────────────────────────────────────────
# STEP 1: GENERATE SEARCH QUERIES FROM CORPUS
# ─────────────────────────────────────────────

def generate_queries() -> set[str]:
    """
    Read the legislation corpus and build a set of exact-match
    search queries from the Act titles.
    e.g. '"Road Traffic Act 1988"'
    """
    if not os.path.exists(CORPUS_FILE):
        log.error(f"Missing {CORPUS_FILE}. Run 2_build_corpus_legislation.py first!")
        return set()

    with open(CORPUS_FILE, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    queries = set()
    for chunk in corpus:
        source = chunk.get("source", "")
        # Check if this chunk is from legislation
        is_leg = source == "legislation" or "ukpga" in str(chunk.get("chunk_id", ""))
        if is_leg:
            title = chunk.get("doc_title")
            if title:
                # Wrap in quotes for exact match
                queries.add(f'"{title}"')

    log.info(f"Generated {len(queries)} unique exact-match queries from corpus.")
    return queries


# ─────────────────────────────────────────────
# STEP 2: SEARCH THE NATIONAL ARCHIVES ATOM FEED
# ─────────────────────────────────────────────

def search_caselaw(session: requests.Session, query_string: str) -> list[str]:
    """
    Search the National Archives Atom feed for a given query.
    Returns a list of direct XML download URLs for matching judgments.
    """
    encoded_query = quote(query_string)
    url = f"{BASE_ATOM_URL}?query={encoded_query}"

    data = get_bytes(session, url)
    if not data:
        return []

    links = []
    try:
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall("atom:entry", ns):
            # The AKN XML document link has type="application/akn+xml"
            for link in entry.findall("atom:link", ns):
                if link.get("type", "") == "application/akn+xml":
                    href = link.get("href")
                    if href:
                        links.append(href)
    except Exception as e:
        log.warning(f"Failed to parse Atom feed for {query_string}: {e}")

    return links


def find_all_judgment_links(session: requests.Session, queries: set[str]) -> list[str]:
    """
    Run all search queries one by one and collect all unique judgment URLs.
    """
    queries_list = sorted(list(queries))
    all_links = set()

    log.info("Searching National Archives...")
    for i, query in enumerate(queries_list, 1):
        links = search_caselaw(session, query)
        if links:
            log.info(f"  [{i}/{len(queries_list)}] {query} -> Found {len(links)} judgments")
            all_links.update(links)
        else:
            log.debug(f"  [{i}/{len(queries_list)}] {query} -> 0 results")

    log.info(f"Total unique judgment links found: {len(all_links)}")
    return list(all_links)


# ─────────────────────────────────────────────
# STEP 3: DOWNLOAD THE JUDGMENT XML FILES
# ─────────────────────────────────────────────

def download_judgments(session: requests.Session, links: list[str]):
    """
    Download each judgment XML file to the caselaw directory.
    Skips files that already exist on disk.
    """
    log.info(f"Downloading {len(links)} unique case law XML documents...")
    success = 0
    skip = 0
    fail = 0

    for i, url in enumerate(links, 1):
        # Build a clean filename from the URL
        # URL looks like: https://caselaw.nationalarchives.gov.uk/uksc/2024/1/data.xml
        parts = url.strip("/").split("/")
        try:
            idx = parts.index("caselaw.nationalarchives.gov.uk")
            core_parts = parts[idx + 1 : -1]  # Drop domain and "data.xml"
            filename = "_".join(core_parts) + ".xml"
        except (ValueError, IndexError):
            filename = f"judgment_{hash(url)}.xml"

        filepath = os.path.join(CASELAW_DIR, filename)

        # Skip if already downloaded
        if os.path.exists(filepath):
            skip += 1
            continue

        # Download the XML
        data = get_bytes(session, url)
        if data and data.strip().startswith(b"<"):
            with open(filepath, "wb") as f:
                f.write(data)
            success += 1
        else:
            fail += 1

        # Print progress every 10 downloads
        if i % 10 == 0:
            log.info(f"  Progress: {i}/{len(links)} (OK={success}, SKIP={skip}, FAIL={fail})")

    log.info(f"Completed! OK={success}  SKIP={skip}  FAIL={fail}  -> {CASELAW_DIR}/")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run():
    """Run the full case law download pipeline."""
    queries = generate_queries()
    if not queries:
        return

    t0 = time.time()

    with build_session() as session:
        # Search for judgment links
        links = find_all_judgment_links(session, queries)

        # Download the actual XML files
        if links:
            download_judgments(session, links)
        else:
            log.info("No judgments found for generated queries.")

    log.info(f"Smart download finished in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    run()
