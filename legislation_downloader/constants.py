"""
constants.py — Static configuration for the legislation downloader.

Contains:
  • API endpoint and request headers
  • Subject slugs, title keywords, year range for the three discovery layers
  • Regex filter used during year-enumeration (client-side)
  • Seed acts that are always downloaded regardless of discovery
  • A lightweight probe URL for rate-limit testing
"""

import re

# ── API ────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.legislation.gov.uk"

HEADERS = {
    "User-Agent": "LegalKGent-Research-Project/3.0 (university-research)",
    "Accept":     "application/xml, application/atom+xml, */*",
}

# Lightweight URL used for connectivity checks and rate-limit probing.
# Must be a small, always-present resource.
PROBE_URL = f"{BASE_URL}/ukpga/1988/52/data.xml"

# Minimum year for Statutory Instruments — older SIs are filtered out.
MIN_SI_YEAR = 2000


# ── Layer 1A: subject-path search ──────────────────────────────────────────
# GET /{ALL_TYPES}/{subject}/data.feed

ALL_TYPES = "ukpga+uksi"   # UK Acts + UK Statutory Instruments

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


# ── Layer 1B: title-keyword search ─────────────────────────────────────────
# GET /title/{encoded_keyword}/data.feed

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


# ── Layer 2: year-enumeration search ───────────────────────────────────────
# GET /{leg_type}/{year}/data.feed  — entire year fetched, then filtered

YEAR_ENUM_RANGE = range(2000, 2027)
YEAR_ENUM_TYPES = ["uksi"]

# Client-side title filter: only keep entries matching these keywords.
YEAR_FILTER_RE = re.compile(
    r"transport|road\s*traffic|highway|motor\s*vehicle|driving|railway"
    r"|aviation|shipping|tachograph|traffic\s*sign|vehicle\s*licen"
    r"|automated\s*vehicle|electric\s*vehicle|taxis|pedicab",
    re.IGNORECASE,
)


# ── Seed acts ──────────────────────────────────────────────────────────────
# Always downloaded, even if the API discovery misses them.
# Format: (leg_type, year, number, title)

SEED_ACTS: list[tuple[str, int, int, str]] = [
    # Primary Acts
    ("ukpga", 2024,  3,   "Automated Vehicles Act 2024"),
    ("ukpga", 2024,  2,   "Pedicabs (London) Act 2024"),
    ("ukpga", 1988, 52,   "Road Traffic Act 1988"),
    ("ukpga", 1988, 53,   "Road Traffic Offenders Act 1988"),
    ("ukpga", 1984, 27,   "Road Traffic Regulation Act 1984"),
    ("ukpga", 2006, 49,   "Road Safety Act 2006"),
    ("ukpga", 2000, 38,   "Transport Act 2000"),
    ("ukpga", 1993, 43,   "Railways Act 1993"),
    ("ukpga", 2005, 14,   "Railways Act 2005"),
    ("ukpga", 2021, 12,   "Air Traffic Management and Unmanned Aircraft Act 2021"),
    ("ukpga", 2012, 19,   "Civil Aviation Act 2012"),
    ("ukpga", 2018, 18,   "Automated and Electric Vehicles Act 2018"),
    ("ukpga", 2022, 14,   "Taxis and Private Hire Vehicles Act 2022"),
    ("ukpga", 1980, 34,   "Highways Act 1980"),
    ("ukpga", 2023, 32,   "Energy Act 2023"),
    # Statutory Instruments
    ("uksi",  2024, 566,  "Goods Vehicles (International Road Transport) Regs 2024"),
    ("uksi",  2024, 615,  "Motor Vehicles (Driving Licences) (Amendment) Regs 2024"),
    ("uksi",  2024, 305,  "Road Vehicles (Registration and Licensing) Regs 2024"),
    ("uksi",  2023, 980,  "Traffic Signs (Amendment) Regulations 2023"),
    ("uksi",  2023, 695,  "Drivers' Hours and Tachographs (Amendment) Regs 2023"),
    ("uksi",  2023, 903,  "Railways (Access, Management) (Amendment) Regs 2023"),
]
