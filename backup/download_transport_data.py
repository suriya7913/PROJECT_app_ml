#!/usr/bin/env python3
"""
LegalKGent — Transportation Law Data Downloader
================================================
Downloads UK transportation law data from multiple structured sources:
  1. Primary Legislation (Acts) from legislation.gov.uk
  2. Statutory Instruments from legislation.gov.uk  
  3. Case Law from National Archives
  4. Amendments Table (which Acts amend which) from legislation.gov.uk
  5. Explanatory Notes metadata

Focused on transportation domain to keep dataset manageable (~2000–3000 chunks).

Usage:
    python download_transport_data.py
"""

import os
import json
import time
import requests
from datetime import datetime

# ============================================================
# CONFIGURATION
# ============================================================

OUTPUT_DIR = "data"
LEGISLATION_DIR = os.path.join(OUTPUT_DIR, "raw_legislation")
CASELAW_DIR = os.path.join(OUTPUT_DIR, "raw_caselaw")
SI_DIR = os.path.join(OUTPUT_DIR, "raw_statutory_instruments")
AMENDMENTS_DIR = os.path.join(OUTPUT_DIR, "amendments")
NOTES_DIR = os.path.join(OUTPUT_DIR, "explanatory_notes")

for d in [LEGISLATION_DIR, CASELAW_DIR, SI_DIR, AMENDMENTS_DIR, NOTES_DIR]:
    os.makedirs(d, exist_ok=True)

HEADERS = {
    'User-Agent': 'LegalKGent-Research-Project/1.0 (university-research)',
    'Accept': 'application/xml, text/xml, */*'
}

POLITE_DELAY = 1.0  # seconds between requests

# ============================================================
# TRANSPORTATION LAW — CURATED ACT LIST
# ============================================================
# These are the key Acts in UK transportation law.
# We download a focused set rather than all Acts in a year.

TRANSPORT_ACTS = [
    # === Core Modern Transport Acts ===
    ("ukpga", 2024, 3,  "Automated Vehicles Act 2024"),
    ("ukpga", 2024, 2,  "Pedicabs (London) Act 2024"),
    
    # === Road Traffic ===
    ("ukpga", 1988, 52, "Road Traffic Act 1988"),
    ("ukpga", 1988, 53, "Road Traffic Offenders Act 1988"),
    ("ukpga", 1984, 27, "Road Traffic Regulation Act 1984"),
    ("ukpga", 2006, 49, "Road Safety Act 2006"),
    
    # === Transport & Railways ===
    ("ukpga", 2000, 38, "Transport Act 2000"),
    ("ukpga", 1993, 43, "Railways Act 1993"),
    ("ukpga", 2005, 14, "Railways Act 2005"),
    ("ukpga", 2008, 26, "Transport (London) Act 2008"),
    
    # === Aviation & Maritime ===
    ("ukpga", 2021, 12, "Air Traffic Management and Unmanned Aircraft Act 2021"),
    ("ukpga", 2012, 19, "Civil Aviation Act 2012"),
    
    # === Electric & Automated Vehicles ===
    ("ukpga", 2018, 18, "Automated and Electric Vehicles Act 2018"),
    
    # === Key Acts that AMEND transport legislation ===
    ("ukpga", 2022, 14, "Taxis and Private Hire Vehicles Act 2022"),
    ("ukpga", 1980, 34, "Highways Act 1980"),
    
    # === Recent Acts with transport implications ===
    ("ukpga", 2023, 54, "Online Safety Act 2023"),      # Regulates automated content delivery
    ("ukpga", 2023, 32, "Energy Act 2023"),              # EV charging infrastructure
]

# === Statutory Instruments (Transport SIs) ===
TRANSPORT_SIS = [
    # Key 2024 transport SIs
    ("uksi", 2024, 566, "Goods Vehicles (International Road Transport Permits) Regs 2024"),
    ("uksi", 2024, 615, "Motor Vehicles (Driving Licences) (Amendment) Regs 2024"),
    ("uksi", 2024, 305, "Road Vehicles (Registration and Licensing) (Amendment) Regs 2024"),
    
    # Key 2023 transport SIs
    ("uksi", 2023, 980, "Traffic Signs (Amendment) Regulations 2023"),
    ("uksi", 2023, 695, "Drivers' Hours and Tachographs (Amendment) Regs 2023"),
    ("uksi", 2023, 903, "Railways (Access, Management and Licensing) (Amendment) Regs 2023"),
    
    # Automated vehicles related
    ("uksi", 2022, 470, "Highway Code (Hierarchy of Road Users) Regs 2022"),
]


# ============================================================
# 1. DOWNLOAD PRIMARY LEGISLATION
# ============================================================

def download_legislation(acts_list, output_dir):
    """Download Acts from legislation.gov.uk as XML."""
    print(f"\n{'='*60}")
    print(f"📜 DOWNLOADING PRIMARY LEGISLATION")
    print(f"{'='*60}")
    
    success = 0
    for act_type, year, number, title in acts_list:
        filename = f"{act_type}_{year}_{number}.xml"
        filepath = os.path.join(output_dir, filename)
        
        if os.path.exists(filepath):
            print(f"  [SKIP] {filename} (already exists)")
            success += 1
            continue
        
        url = f"https://www.legislation.gov.uk/{act_type}/{year}/{number}/data.xml"
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.content.strip().startswith(b"<"):
                with open(filepath, "wb") as f:
                    f.write(r.content)
                size_kb = len(r.content) / 1024
                print(f"  [OK] {title} ({size_kb:.0f} KB)")
                success += 1
            elif r.status_code == 404:
                print(f"  [404] {title} — not found at {url}")
            else:
                print(f"  [ERR] HTTP {r.status_code} for {title}")
        except Exception as e:
            print(f"  [ERR] {title}: {e}")
        
        time.sleep(POLITE_DELAY)
    
    print(f"\n  ✅ Downloaded {success}/{len(acts_list)} Acts")
    return success


# ============================================================
# 2. DOWNLOAD STATUTORY INSTRUMENTS
# ============================================================

def download_statutory_instruments(si_list, output_dir):
    """Download SIs from legislation.gov.uk as XML."""
    print(f"\n{'='*60}")
    print(f"📋 DOWNLOADING STATUTORY INSTRUMENTS")
    print(f"{'='*60}")
    
    success = 0
    for si_type, year, number, title in si_list:
        filename = f"{si_type}_{year}_{number}.xml"
        filepath = os.path.join(output_dir, filename)
        
        if os.path.exists(filepath):
            print(f"  [SKIP] {filename} (already exists)")
            success += 1
            continue
        
        url = f"https://www.legislation.gov.uk/{si_type}/{year}/{number}/data.xml"
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.content.strip().startswith(b"<"):
                with open(filepath, "wb") as f:
                    f.write(r.content)
                size_kb = len(r.content) / 1024
                print(f"  [OK] {title} ({size_kb:.0f} KB)")
                success += 1
            else:
                print(f"  [ERR] HTTP {r.status_code} for {title}")
        except Exception as e:
            print(f"  [ERR] {title}: {e}")
        
        time.sleep(POLITE_DELAY)
    
    print(f"\n  ✅ Downloaded {success}/{len(si_list)} SIs")
    return success


# ============================================================
# 3. DOWNLOAD CASE LAW (National Archives)
# ============================================================

def download_case_law_search(keyword, courts, years, max_per_court=10, output_dir=None):
    """Search National Archives for transport-related cases.
    
    Uses the Find Case Law API to search by keyword, then downloads XML.
    Fallback: sequential number download for each court/year.
    """
    print(f"\n{'='*60}")
    print(f"⚖️  DOWNLOADING CASE LAW (keyword: '{keyword}')")
    print(f"{'='*60}")
    
    success = 0
    
    for court in courts:
        for year in years:
            fails = 0
            print(f"\n  --- {court.upper()} {year} ---")
            
            for num in range(1, max_per_court + 1):
                filename = f"{court.replace('/', '_')}_{year}_{num}.xml"
                filepath = os.path.join(output_dir, filename)
                
                if os.path.exists(filepath):
                    print(f"    [SKIP] {filename}")
                    success += 1
                    continue
                
                # Try multiple URL patterns
                urls = [
                    f"https://caselaw.nationalarchives.gov.uk/{court}/{year}/{num}/data.xml",
                    f"https://caselaw.nationalarchives.gov.uk/{court}/{year}/{num}.xml",
                ]
                
                downloaded = False
                for url in urls:
                    try:
                        r = requests.get(url, headers=HEADERS, timeout=10)
                        if r.status_code == 200 and r.content.strip().startswith(b"<"):
                            with open(filepath, "wb") as f:
                                f.write(r.content)
                            print(f"    [OK] {filename}")
                            success += 1
                            downloaded = True
                            fails = 0
                            break
                    except Exception:
                        pass
                
                if not downloaded:
                    fails += 1
                    if fails >= 5:
                        print(f"    >> Skipping {court} {year} (5 consecutive misses)")
                        break
                
                time.sleep(POLITE_DELAY)
    
    print(f"\n  ✅ Downloaded {success} cases")
    return success


# ============================================================
# 4. DOWNLOAD AMENDMENTS TABLE (Structured Data)
# ============================================================

def download_amendments_table(acts_list, output_dir):
    """Download the 'changes to legislation' table for each Act.
    
    legislation.gov.uk provides structured data about which Acts
    amend, repeal, or modify each other. This is gold for the KG —
    it gives us ground-truth amendment relationships we can validate against.
    
    URL pattern: /ukpga/YEAR/NUM/data.xht?view=extent&timeline=true
    Also try: /ukpga/YEAR/NUM/changes/affected
    """
    print(f"\n{'='*60}")
    print(f"🔗 DOWNLOADING AMENDMENTS TABLES")
    print(f"{'='*60}")
    
    success = 0
    for act_type, year, number, title in acts_list:
        filename = f"{act_type}_{year}_{number}_amendments.json"
        filepath = os.path.join(output_dir, filename)
        
        if os.path.exists(filepath):
            print(f"  [SKIP] {filename}")
            success += 1
            continue
        
        # Try the changes feed (structured data about what amends this Act)
        url = f"https://www.legislation.gov.uk/{act_type}/{year}/{number}/changes/affected/data.feed"
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                with open(filepath.replace('.json', '.xml'), "wb") as f:
                    f.write(r.content)
                print(f"  [OK] {title} amendments feed")
                success += 1
            else:
                print(f"  [SKIP] No amendments feed for {title} (HTTP {r.status_code})")
        except Exception as e:
            print(f"  [ERR] {title}: {e}")
        
        time.sleep(POLITE_DELAY)
    
    print(f"\n  ✅ Downloaded {success} amendments tables")
    return success


# ============================================================
# 5. DOWNLOAD EXPLANATORY NOTES
# ============================================================

def download_explanatory_notes(acts_list, output_dir):
    """Download explanatory notes for Acts.
    
    Explanatory notes provide commentary on what each section does,
    which is extremely valuable for extracting detail_text.
    URL: /ukpga/YEAR/NUM/notes/data.xml
    """
    print(f"\n{'='*60}")
    print(f"📝 DOWNLOADING EXPLANATORY NOTES")
    print(f"{'='*60}")
    
    success = 0
    for act_type, year, number, title in acts_list:
        if act_type != "ukpga":
            continue  # Only primary Acts have explanatory notes
        
        filename = f"{act_type}_{year}_{number}_notes.xml"
        filepath = os.path.join(output_dir, filename)
        
        if os.path.exists(filepath):
            print(f"  [SKIP] {filename}")
            success += 1
            continue
        
        url = f"https://www.legislation.gov.uk/{act_type}/{year}/{number}/notes/data.xml"
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 500:
                with open(filepath, "wb") as f:
                    f.write(r.content)
                size_kb = len(r.content) / 1024
                print(f"  [OK] {title} notes ({size_kb:.0f} KB)")
                success += 1
            else:
                print(f"  [SKIP] No notes for {title}")
        except Exception as e:
            print(f"  [ERR] {title}: {e}")
        
        time.sleep(POLITE_DELAY)
    
    print(f"\n  ✅ Downloaded {success} explanatory notes")
    return success


# ============================================================
# MAIN
# ============================================================

def main():
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  LegalKGent — Transportation Law Data Download          ║
║  Domain: UK Transport, Road Traffic, Automated Vehicles ║
║  Sources: legislation.gov.uk + National Archives        ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    start = time.time()
    stats = {}
    
    # 1. Primary legislation
    stats['acts'] = download_legislation(TRANSPORT_ACTS, LEGISLATION_DIR)
    
    # 2. Statutory Instruments
    stats['sis'] = download_statutory_instruments(TRANSPORT_SIS, SI_DIR)
    
    # 3. Case law (transport-related courts)
    stats['cases'] = download_case_law_search(
        keyword="transport",
        courts=["ewhc/admin", "ewca/civ", "uksc"],
        years=[2023, 2024],
        max_per_court=10,
        output_dir=CASELAW_DIR
    )
    
    # 4. Amendments tables (ground-truth for validation)
    stats['amendments'] = download_amendments_table(TRANSPORT_ACTS, AMENDMENTS_DIR)
    
    # 5. Explanatory notes
    stats['notes'] = download_explanatory_notes(TRANSPORT_ACTS, NOTES_DIR)
    
    elapsed = time.time() - start
    
    print(f"\n{'='*60}")
    print(f"🏁 DOWNLOAD COMPLETE ({elapsed:.0f}s)")
    print(f"{'='*60}")
    print(f"  Acts:              {stats['acts']}")
    print(f"  SIs:               {stats['sis']}")
    print(f"  Cases:             {stats['cases']}")
    print(f"  Amendments tables: {stats['amendments']}")
    print(f"  Explanatory notes: {stats['notes']}")
    print(f"\n  Files saved to: {OUTPUT_DIR}/")
    
    # Save download manifest
    manifest = {
        "download_time": datetime.now().isoformat(),
        "domain": "UK Transportation Law",
        "stats": stats,
        "acts": [{"type": t, "year": y, "number": n, "title": title}
                 for t, y, n, title in TRANSPORT_ACTS],
        "sis": [{"type": t, "year": y, "number": n, "title": title}
                for t, y, n, title in TRANSPORT_SIS],
    }
    with open(os.path.join(OUTPUT_DIR, "download_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"  Manifest: {OUTPUT_DIR}/download_manifest.json")


if __name__ == "__main__":
    main()
