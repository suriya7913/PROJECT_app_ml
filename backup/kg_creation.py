# -*- coding: utf-8 -*-
"""
LegalKGent — Robust Knowledge Graph Creation Pipeline
======================================================
Smart XML chunking + Normalized triple extraction + Neo4j ingestion.
Copy each CELL block into a separate Jupyter/Colab cell.
"""

################################################################
# CELL 1 — Installs & Ollama Setup (Kaggle / Colab)
# Run this cell first. Works on both Kaggle and Colab.
################################################################

# import os, subprocess, time
#
# # Install dependencies
# os.system('apt-get install -y zstd')   # required by new Ollama installer
# os.system('pip install ollama neo4j pyvis networkx -q')
#
# # GPU library path (Colab: /usr/lib64-nvidia, Kaggle: /usr/lib/x86_64-linux-gnu)
# os.environ['LD_LIBRARY_PATH'] = '/usr/lib/x86_64-linux-gnu'
# os.environ['CUDA_VISIBLE_DEVICES'] = '0,1'       # use both T4 GPUs
# os.environ['OLLAMA_NUM_PARALLEL'] = '4'           # 4 parallel LLM requests
# os.environ['OLLAMA_GPU_LAYERS'] = '999'           # offload all layers to GPU
#
# # Install Ollama
# os.system('curl -fsSL https://ollama.com/install.sh | sh')
#
# # Start Ollama server (subprocess works on Kaggle, nohup does not)
# subprocess.Popen(
#     ['ollama', 'serve'],
#     stdout=open('ollama.log', 'w'),
#     stderr=subprocess.STDOUT,
#     env=os.environ
# )
# print('⏳ Waiting for Ollama to start...')
# time.sleep(15)
#
# # Pull model
# os.system('ollama pull qwen2.5:7b')
# os.system('ollama list')
# os.system('nvidia-smi')
# print('✅ Ollama ready with GPU')

################################################################
# CELL 2 — Imports & Configuration
################################################################

import os
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

# --- PATHS ---
RAW_LEGISLATION_DIR = "data/raw_legislation"
RAW_CASELAW_DIR = "data/raw_caselaw"
CORPUS_FILE = "data/legal_corpus_final.json"
SMART_CORPUS_FILE = "data/smart_corpus.json"
OUTPUT_FILE = "data/extracted_triples.json"
MODEL_NAME = "qwen2.5:7b"

# --- GPU OPTIMIZATION (RunPod / A40) ---
# Start Ollama with these env vars BEFORE running this script:
#   export OLLAMA_NUM_PARALLEL=8        # 8 concurrent inference slots
#   export OLLAMA_FLASH_ATTENTION=1     # Flash attention = less VRAM per slot
#   export OLLAMA_KV_CACHE_TYPE=q8_0    # Quantized KV cache = less VRAM per slot
#   ollama serve &
#
# VRAM budget (A40 48GB): qwen2.5:7b ~5GB + 8 slots × ~1.5GB KV = ~17GB
# For qwen2.5:14b: ~10GB + 4 slots × ~2GB KV = ~18GB (set NUM_WORKERS=4)
# For qwen2.5:32b: ~20GB + 3 slots × ~4GB KV = ~32GB (set NUM_WORKERS=3)
BATCH_SIZE = 5000

# --- CANONICAL ACTIONS ---
# The ONLY valid relationship types in our KG
CANONICAL_ACTIONS = [
    "AMENDS", "REPEALS", "SUBSTITUTES", "INSERTS",
    "COMMENCES", "REVOKES", "APPLIES", "CITES", "OVERRULES",
    # Extended actions for broader data sources (SIs, Explanatory Notes, etc.)
    "DEFINES", "INTERPRETS", "DELEGATES", "IMPLEMENTS",
]

# Map any LLM variation to the canonical form
ACTION_NORMALIZER = {
    "AMEND": "AMENDS", "AMENDED": "AMENDS", "AMENDS": "AMENDS", "AMENDING": "AMENDS",
    "REPEAL": "REPEALS", "REPEALED": "REPEALS", "REPEALS": "REPEALS", "REPEALING": "REPEALS",
    "OMIT": "REPEALS", "OMITS": "REPEALS", "OMITTED": "REPEALS",  # "is omitted" = repeal
    "SUBSTITUTE": "SUBSTITUTES", "SUBSTITUTED": "SUBSTITUTES", "SUBSTITUTES": "SUBSTITUTES",
    "REPLACE": "SUBSTITUTES", "REPLACES": "SUBSTITUTES", "REPLACED": "SUBSTITUTES",
    "INSERT": "INSERTS", "INSERTED": "INSERTS", "INSERTS": "INSERTS", "INSERTING": "INSERTS",
    "COMMENCE": "COMMENCES", "COMMENCED": "COMMENCES", "COMMENCES": "COMMENCES",
    "REVOKE": "REVOKES", "REVOKED": "REVOKES", "REVOKES": "REVOKES",
    "APPLY": "APPLIES", "APPLIED": "APPLIES", "APPLIES": "APPLIES",
    "CITE": "CITES", "CITED": "CITES", "CITES": "CITES", "CITING": "CITES",
    "OVERRULE": "OVERRULES", "OVERRULED": "OVERRULES", "OVERRULES": "OVERRULES",
    "RELATES_TO": "CITES",  # fallback
    # Extended actions
    "DEFINE": "DEFINES", "DEFINED": "DEFINES", "DEFINES": "DEFINES", "DEFINING": "DEFINES",
    "INTERPRET": "INTERPRETS", "INTERPRETED": "INTERPRETS", "INTERPRETS": "INTERPRETS",
    "DELEGATE": "DELEGATES", "DELEGATED": "DELEGATES", "DELEGATES": "DELEGATES",
    "IMPLEMENT": "IMPLEMENTS", "IMPLEMENTED": "IMPLEMENTS", "IMPLEMENTS": "IMPLEMENTS",
    "TRANSPOSES": "IMPLEMENTS", "TRANSPOSED": "IMPLEMENTS",  # EU law transposition
}

print("✅ Cell 2 done — Config loaded")

################################################################
# CELL 3 — Smart XML Flattener (Part A)
# Re-parses raw XML to extract CLML structural metadata
################################################################

# CLML namespaces — needed because ElementTree requires full namespace URIs
LEG_NS = 'http://www.legislation.gov.uk/namespaces/legislation'
META_NS = 'http://www.legislation.gov.uk/namespaces/metadata'
DC_NS = 'http://purl.org/dc/elements/1.1/'
AKN_NS = 'http://docs.oasis-open.org/legaldocml/ns/akn/3.0'
TNA_NS = 'https://caselaw.nationalarchives.gov.uk/akn'

NAMESPACES = {
    'leg': LEG_NS,
    'ukm': META_NS,
    'dc': DC_NS,
    'akn': AKN_NS,
    'tna': TNA_NS,
}


def _leg(tag):
    """Prefix a tag with the legislation namespace."""
    return f'{{{LEG_NS}}}{tag}'


def _akn(tag):
    """Prefix a tag with the AKN namespace."""
    return f'{{{AKN_NS}}}{tag}'


def extract_text_recursive(elem):
    """Extract all text from an XML element and its children, stripping tags."""
    parts = []
    if elem.text:
        parts.append(elem.text.strip())
    for child in elem:
        parts.append(extract_text_recursive(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)


def extract_defined_terms(root):
    """Find all <Term> definitions in the XML: 'the Act' -> 'Local Government Finance Act 1988'."""
    terms = {}
    for term_elem in root.iter(_leg('Term')):
        term_text = extract_text_recursive(term_elem)
        if term_text:
            # Try to find the full name nearby (parent text before the Term element)
            parent = None
            for p in root.iter():
                if term_elem in list(p):
                    parent = p
                    break
            if parent is not None:
                parent_text = extract_text_recursive(parent)
                # Pattern: "the Local Government Finance Act 1988 ("the Act")"
                match = re.search(
                    r'(?:the\s+)?([A-Z][A-Za-z\s,()]+?Act\s+\d{4})\s*\(\s*["\u201c]' + re.escape(term_text),
                    parent_text
                )
                if match:
                    terms[term_text] = match.group(1).strip()
    return terms


def extract_internal_links(section_elem):
    """Extract all <InternalLink> references from a section."""
    refs = []
    for link in section_elem.iter(_leg('InternalLink')):
        ref_text = extract_text_recursive(link)
        if ref_text:
            refs.append(ref_text)
    return refs


def extract_inline_amendments(section_elem):
    """Extract all <InlineAmendment> text from a section."""
    amendments = []
    for amend in section_elem.iter(_leg('InlineAmendment')):
        amend_text = extract_text_recursive(amend)
        if amend_text:
            amendments.append(amend_text)
    return amendments


def get_parent_pblock_title(elem, root):
    """Walk up the tree to find the nearest <Pblock><Title> ancestor."""
    parent_map = {c: p for p in root.iter() for c in p}

    current = elem
    while current is not None:
        tag_local = current.tag.split('}')[-1] if '}' in current.tag else current.tag
        if tag_local in ('Pblock', 'Part'):
            title_elem = current.find(_leg('Title'))
            if title_elem is not None:
                return extract_text_recursive(title_elem)
        current = parent_map.get(current)
    return None


def parse_legislation_xml(filepath):
    """Parse a CLML legislation XML file into smart chunks."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    ⚠️ XML parse error: {filepath}: {e}")
        return []

    # Extract filename-based ID prefix
    filename = os.path.basename(filepath).replace('.xml', '')

    # Extract document title from PrimaryPrelims/Title (namespaced)
    doc_title = filename
    prelim_title = root.find(f'.//{_leg("PrimaryPrelims")}/{_leg("Title")}')
    if prelim_title is not None:
        doc_title = extract_text_recursive(prelim_title)
    else:
        # Fallback: any Title element
        title_elem = root.find(f'.//{_leg("Title")}')
        if title_elem is not None:
            doc_title = extract_text_recursive(title_elem)

    # Extract year from Number element or filename
    number_elem = root.find(f'.//{_leg("Number")}')
    year_text = extract_text_recursive(number_elem) if number_elem is not None else filename
    year_match = re.search(r'(\d{4})', year_text)
    year = year_match.group(1) if year_match else "unknown"

    # Extract document-level defined terms
    defined_terms = extract_defined_terms(root)

    # Extract date of enactment
    date_elem = root.find(f'.//{_leg("DateOfEnactment")}/{_leg("DateText")}')
    enactment_date = None
    if date_elem is not None:
        date_text = extract_text_recursive(date_elem)
        date_match = re.search(r'(\d{1,2})\w*\s+(\w+)\s+(\d{4})', date_text)
        if date_match:
            try:
                from datetime import datetime
                enactment_date = datetime.strptime(
                    f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}",
                    "%d %B %Y"
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

    chunks = []

    # Find all provision elements (P1 = sections, P1group = grouped sections)
    for section_elem in root.iter():
        tag_local = section_elem.tag.split('}')[-1] if '}' in section_elem.tag else section_elem.tag
        if tag_local not in ('P1', 'P1group'):
            continue

        # Get section number
        pnum_elem = section_elem.find(f'.//{_leg("Pnumber")}')
        section_num = extract_text_recursive(pnum_elem) if pnum_elem is not None else None

        # Also check the element's attributes for section id
        doc_uri = section_elem.get('DocumentURI', '')
        id_attr = section_elem.get('id', '')

        # Build section identifier
        if section_num:
            section_id = section_num
        elif id_attr:
            section_id = id_attr.replace('section-', '').replace('schedule-', 'SCHEDULE ')
        else:
            continue

        chunk_id = f"{filename}.xml_{section_id}"

        # Get text content
        content = extract_text_recursive(section_elem)
        if not content or len(content) < 20:
            continue

        # Get section heading
        heading = None
        title_elem = section_elem.find(_leg('Title'))
        if title_elem is not None:
            heading = extract_text_recursive(title_elem)

        # Get structural metadata from XML attributes
        extent = section_elem.get('RestrictExtent', None)
        in_force_date = section_elem.get('RestrictStartDate', None)

        # Walk up to find parent block title
        part_title = get_parent_pblock_title(section_elem, root)

        # Extract inline amendments and cross-references
        internal_refs = extract_internal_links(section_elem)
        inline_amendments = extract_inline_amendments(section_elem)

        # Build the contextual content prefix
        content_prefix = f"ACT: {doc_title} ({year}) | SECTION: {section_id}"
        if part_title:
            content_prefix += f" | PART: {part_title}"
        if heading:
            content_prefix += f" | HEADING: {heading}"
        content_prefix += f" | TEXT: "

        chunk = {
            "id": chunk_id,
            "source": "legislation",
            "doc_title": doc_title,
            "year": year,
            "section": str(section_id),
            "part": part_title,
            "heading": heading,
            "extent": extent,
            "in_force_date": in_force_date or enactment_date,
            "internal_refs": internal_refs if internal_refs else None,
            "inline_amendments": inline_amendments if inline_amendments else None,
            "defined_terms": defined_terms if defined_terms else None,
            "content": content_prefix + content
        }
        chunks.append(chunk)

    # Also parse Schedules
    for schedule in root.iter(_leg('Schedule')):
        sched_num = schedule.find(_leg('Number'))
        sched_id = extract_text_recursive(sched_num) if sched_num is not None else "SCHEDULE"

        for para in schedule.iter(_leg('P1')):
            pnum = para.find(f'.//{_leg("Pnumber")}')
            para_num = extract_text_recursive(pnum) if pnum is not None else ""
            para_id = f"{sched_id}" + (f"_{para_num}" if para_num else "")
            chunk_id = f"{filename}.xml_{para_id}"

            content = extract_text_recursive(para)
            if not content or len(content) < 20:
                continue

            extent = para.get('RestrictExtent', schedule.get('RestrictExtent'))
            in_force = para.get('RestrictStartDate', schedule.get('RestrictStartDate'))

            chunk = {
                "id": chunk_id,
                "source": "legislation",
                "doc_title": doc_title,
                "year": year,
                "section": para_id,
                "part": sched_id,
                "heading": None,
                "extent": extent,
                "in_force_date": in_force or enactment_date,
                "internal_refs": extract_internal_links(para) or None,
                "inline_amendments": extract_inline_amendments(para) or None,
                "defined_terms": defined_terms if defined_terms else None,
                "content": f"ACT: {doc_title} ({year}) | SECTION: {para_id} | TEXT: {content}"
            }
            chunks.append(chunk)

    return chunks


def parse_caselaw_xml(filepath):
    """Parse a case law XML file into chunks (AKN namespace)."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    ⚠️ Case law parse error: {filepath}: {e}")
        return []

    filename = os.path.basename(filepath).replace('.xml', '')

    # Extract case name — try multiple namespaced tags
    case_name = None

    # Try dc:title
    dc_title = root.find(f'.//{{{DC_NS}}}title')
    if dc_title is not None:
        case_name = extract_text_recursive(dc_title)

    # Try AKN FRBRname
    if not case_name:
        frbr_name = root.find(f'.//{_akn("FRBRname")}')
        if frbr_name is not None:
            case_name = frbr_name.get('value', None) or extract_text_recursive(frbr_name)

    if not case_name:
        case_name = filename

    # Extract year
    year_match = re.search(r'(\d{4})', filename)
    year = year_match.group(1) if year_match else "unknown"

    # Determine court level from filename
    court_level = "Unknown"
    if "uksc_" in filename:
        court_level = "Supreme Court"
    elif "ewca_" in filename:
        court_level = "Court of Appeal"
    elif "ewhc_" in filename:
        court_level = "High Court"

    # Extract paragraphs — try AKN namespace then bare
    chunks = []
    paragraphs = list(root.iter(_akn('paragraph'))) or list(root.iter(_akn('Paragraph')))

    # If no structured paragraphs, extract all text as one chunk
    if not paragraphs:
        full_text = extract_text_recursive(root)
        if full_text and len(full_text) > 50:
            chunks.append({
                "id": f"{filename}.xml_full",
                "source": "judgment",
                "doc_title": case_name,
                "year": year,
                "section": "full",
                "part": None,
                "heading": None,
                "extent": None,
                "in_force_date": None,
                "court_level": court_level,
                "internal_refs": None,
                "inline_amendments": None,
                "defined_terms": None,
                "content": f"CASE: {case_name} ({year}) | COURT: {court_level} | TEXT: {full_text}"
            })
        return chunks

    for i, para in enumerate(paragraphs):
        para_text = extract_text_recursive(para)
        if not para_text or len(para_text) < 30:
            continue

        para_num = para.get('Number', para.get('eId', str(i + 1)))

        chunks.append({
            "id": f"{filename}.xml_{para_num}",
            "source": "judgment",
            "doc_title": case_name,
            "year": year,
            "section": str(para_num),
            "part": None,
            "heading": None,
            "extent": None,
            "in_force_date": None,
            "court_level": court_level,
            "internal_refs": None,
            "inline_amendments": None,
            "defined_terms": None,
            "content": f"CASE: {case_name} ({year}) | COURT: {court_level} | PARA: {para_num} | TEXT: {para_text}"
        })

    return chunks


def build_smart_corpus():
    """Parse all raw XML files into smart chunks with CLML metadata."""
    all_chunks = []

    # Parse legislation
    if os.path.exists(RAW_LEGISLATION_DIR):
        xml_files = sorted([f for f in os.listdir(RAW_LEGISLATION_DIR) if f.endswith('.xml')])
        print(f"📂 Found {len(xml_files)} legislation XML files")
        for f in xml_files:
            chunks = parse_legislation_xml(os.path.join(RAW_LEGISLATION_DIR, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    else:
        print(f"⚠️ No legislation directory: {RAW_LEGISLATION_DIR}")

    # Parse case law
    if os.path.exists(RAW_CASELAW_DIR):
        xml_files = sorted([f for f in os.listdir(RAW_CASELAW_DIR) if f.endswith('.xml')])
        print(f"📂 Found {len(xml_files)} case law XML files")
        for f in xml_files:
            chunks = parse_caselaw_xml(os.path.join(RAW_CASELAW_DIR, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    else:
        print(f"⚠️ No case law directory: {RAW_CASELAW_DIR}")

    # Save
    with open(SMART_CORPUS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Smart corpus built: {len(all_chunks)} chunks → {SMART_CORPUS_FILE}")
    return all_chunks


# Build or load smart corpus
if os.path.exists(SMART_CORPUS_FILE):
    print(f"📂 Loading existing smart corpus from {SMART_CORPUS_FILE}")
    with open(SMART_CORPUS_FILE, "r", encoding="utf-8") as f:
        smart_corpus = json.load(f)
    print(f"   {len(smart_corpus)} chunks loaded")
else:
    print("🔨 Building smart corpus from raw XML...")
    smart_corpus = build_smart_corpus()

################################################################
# CELL 4 — Corpus Stats & Quality Check
################################################################

sources = defaultdict(int)
with_date = 0
with_extent = 0
with_amendments = 0
with_terms = 0

for c in smart_corpus:
    sources[c.get('source', 'unknown')] += 1
    if c.get('in_force_date'):
        with_date += 1
    if c.get('extent'):
        with_extent += 1
    if c.get('inline_amendments'):
        with_amendments += 1
    if c.get('defined_terms'):
        with_terms += 1

print(f"📊 Smart Corpus Stats:")
print(f"   Total chunks: {len(smart_corpus)}")
print(f"   Sources: {dict(sources)}")
print(f"   With in_force_date: {with_date}")
print(f"   With extent: {with_extent}")
print(f"   With inline_amendments: {with_amendments}")
print(f"   With defined_terms: {with_terms}")

# Show a sample smart chunk
print(f"\n📝 Sample smart chunk:")
sample = next((c for c in smart_corpus if c.get('inline_amendments')), smart_corpus[0])
print(json.dumps({k: v for k, v in sample.items() if k != 'content'}, indent=2, default=str))
print(f"   content: {sample['content'][:200]}...")


################################################################
# CELL 5 — Normalization Functions (Part B)
################################################################

def normalize_action(raw_action):
    """Map any LLM action string to canonical form. Returns None if unrecognized."""
    if not raw_action:
        return None
    normalized = ACTION_NORMALIZER.get(raw_action.upper().strip())
    if normalized:
        return normalized
    # Fuzzy fallback: check if any canonical action is contained
    upper = raw_action.upper().strip()
    for canon in CANONICAL_ACTIONS:
        if canon in upper or upper in canon:
            return canon
    return None


def build_abbreviation_table(corpus):
    """
    Auto-extract abbreviation definitions from corpus text.
    Looks for patterns like: 'the Landlord and Tenant Act 1985 ("the LTA 1985")'
    Also uses <Term> definitions extracted during XML parsing.
    """
    abbrev_table = {}

    # 1. From defined_terms in smart chunks
    for chunk in corpus:
        terms = chunk.get('defined_terms')
        if terms and isinstance(terms, dict):
            for short, full in terms.items():
                if short and full and len(short) < len(full):
                    abbrev_table[short] = full

    # 2. From text patterns: '... Full Act Name Year ("abbreviation")'
    pattern = re.compile(
        r'((?:the\s+)?[A-Z][A-Za-z\s,\'-]+?Act\s+\d{4})\s*'
        r'\(\s*["\u201c]\s*((?:the\s+)?[A-Z][A-Za-z\s]+?\d{4})\s*["\u201d]\s*\)'
    )
    for chunk in corpus:
        content = chunk.get('content', '')
        for match in pattern.finditer(content):
            full_name = match.group(1).strip()
            abbreviation = match.group(2).strip()
            if abbreviation and full_name and len(abbreviation) < len(full_name):
                abbrev_table[abbreviation] = full_name

    print(f"📚 Abbreviation table: {len(abbrev_table)} entries")
    for short, full in sorted(abbrev_table.items()):
        print(f"   {short} → {full}")

    return abbrev_table


def build_id_to_title_map(corpus):
    """Map source ID prefixes to readable Act titles."""
    id_to_title = {}
    for c in corpus:
        chunk_id = c.get('id', '')
        prefix = chunk_id.rsplit('.xml_', 1)[0] if '.xml_' in chunk_id else chunk_id
        if prefix and prefix not in id_to_title:
            id_to_title[prefix] = c.get('doc_title', prefix)
    return id_to_title


def normalize_citation(raw_citation, abbrev_table=None):
    """
    Normalize a citation to consistent format.
    - Expand abbreviations (LRA 1967 → Leasehold Reform Act 1967)
    - Standardize section refs: 'section 5' → 's.5', 'Schedule 2' → 'Sch.2'
    """
    if not raw_citation:
        return None

    citation = raw_citation.strip()

    # 1. Expand known abbreviations
    if abbrev_table:
        for short, full in abbrev_table.items():
            if short in citation:
                citation = citation.replace(short, full)

    # 2. Normalize section references (but be careful not to break Act names)
    # Only normalize AFTER the Act name (year pattern)
    year_match = re.search(r'\d{4}', citation)
    if year_match:
        pos = year_match.end()
        act_part = citation[:pos]
        ref_part = citation[pos:]

        # Normalize reference part
        ref_part = re.sub(r'\bsection\s+', 's.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bsections\s+', 'ss.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bSchedule\s+', 'Sch.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bSCHEDULE\s+', 'Sch.', ref_part)
        ref_part = re.sub(r'\bparagraph\s+', 'para.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bparagraphs\s+', 'paras.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bregulation\s+', 'reg.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\bregulations\s+', 'regs.', ref_part, flags=re.IGNORECASE)
        ref_part = re.sub(r'\barticle\s+', 'art.', ref_part, flags=re.IGNORECASE)

        citation = act_part + ref_part

    return citation.strip()


def extract_act_name(citation):
    """Pull out just the Act/Case name without section reference.
    'Housing Act 1996 s.122' → 'Housing Act 1996'
    """
    if not citation:
        return None
    # Match: words ending with Act/Bill/Order + year
    match = re.match(r'(.*?(?:Act|Bill|Order|Regulations?|Rules?)\s+\d{4})', citation)
    if match:
        return match.group(1).strip()
    return citation.strip()


# Build the lookup tables
abbrev_table = build_abbreviation_table(smart_corpus)
id_to_title = build_id_to_title_map(smart_corpus)

print(f"\n📋 ID → Title map: {len(id_to_title)} entries")
for sid, title in sorted(id_to_title.items())[:10]:
    print(f"   {sid} = {title}")
print("   ...")


################################################################
# CELL 6 — Improved System Prompt
################################################################

import ollama

SYSTEM_PROMPT = """You are a UK Legal Knowledge Engineer. Extract ALL legal relationships from the given text.

RELATIONSHIP TYPES (use EXACTLY these names):
- AMENDS: modifies another law ("is amended", "for X substitute Y", "after X insert Y")
- REPEALS: removes/omits another law ("is repealed", "shall cease to have effect", "is omitted", "are omitted")
- SUBSTITUTES: replaces specific text/wording ("for 'X' substitute 'Y'")
- INSERTS: adds new provisions ("after section X insert")
- COMMENCES: brings a law into force ("comes into force on", "commencement order")
- REVOKES: removes secondary legislation ("is revoked")
- OVERRULES: court overrules a previous case ("overruled", "departed from")
- APPLIES: law applies to a scope ("applies to England and Wales")
- CITES: simple reference without modification

RULES:
1. Use formal FULL citations — e.g., "Welfare Reform Act 2012 s.3", "[2023] UKSC 1"
2. NEVER use abbreviations like "LRA 1967" or "the Act" — always expand to the full Act name
3. Capture effective_date in YYYY-MM-DD format if mentioned
4. For SUBSTITUTES, capture the NEW text in detail_text
5. Return empty array [] if NO relationships found
6. Do NOT hallucinate relationships not explicitly stated in the text
7. Each relationship should have exactly ONE target — if text says "sections 5, 6 and 7 are repealed", create THREE separate relationships

BAD EXAMPLES (do NOT produce these):
- {"action": "INSERT", ...}  ← wrong, use "INSERTS"
- {"action": "REPLACES", ...}  ← wrong, use "SUBSTITUTES"
- {"target_citation": "the Act"}  ← wrong, use full name
- {"target_citation": "LRA 1967"}  ← wrong, use "Leasehold Reform Act 1967"
- {"target_citation": "sections 5, 6 and 7"}  ← wrong, split into separate objects

Respond with ONLY a JSON array. Each object must have:
{"action": "...", "target_citation": "...", "detail_text": "..." or null, "effective_date": "YYYY-MM-DD" or null}

Example output:
[{"action": "REPEALS", "target_citation": "Welfare Reform Act 2012 s.3", "detail_text": null, "effective_date": "2024-01-01"}]"""

print("✅ Cell 6 done — System prompt defined")


################################################################
# CELL 6B — Graph-Aware Extraction Context (MedKGent Constructor Agent Pattern)
################################################################

def graph_context_for_chunk(chunk, neo4j_driver=None, max_context_triples=15):
    """Query Neo4j for existing triples related to Acts mentioned in this chunk.
    
    Inspired by MedKGent's Constructor Agent which checks the existing graph
    before inserting triples, giving the LLM context to:
    - Avoid contradicting existing knowledge
    - Produce richer detail_text
    - Maintain consistent entity naming
    
    Args:
        chunk: Dict with 'content', 'doc_title', etc.
        neo4j_driver: Neo4j driver instance (optional, returns empty if None)
        max_context_triples: Max number of existing triples to include
    
    Returns:
        String of existing triples formatted as context, or empty string.
    """
    if neo4j_driver is None:
        return ""
    
    # Extract Act names from chunk text
    content = chunk.get('content', '')
    doc_title = chunk.get('doc_title', '')
    
    # Find Act references in the text
    act_pattern = r'([A-Z][A-Za-z\s\(\)]+(?:Act|Order|Regulations?)\s+\d{4})'
    act_mentions = set(re.findall(act_pattern, content))
    if doc_title:
        act_mentions.add(doc_title)
    
    if not act_mentions:
        return ""
    
    context_lines = []
    try:
        with neo4j_driver.session() as session:
            for act_name in list(act_mentions)[:5]:  # Limit to 5 Acts
                # Search by source title or target citation
                result = session.run("""
                    MATCH (s:LegalDoc)-[r:LEGAL_RELATIONSHIP]->(t:LegalDoc)
                    WHERE s.title CONTAINS $act_name OR t.citation CONTAINS $act_name
                    RETURN s.id AS source, r.action_type AS action, 
                           t.citation AS target, r.detail AS detail
                    LIMIT $limit
                """, act_name=act_name.strip(), limit=max_context_triples)
                
                for record in result:
                    detail_part = f" | {record['detail'][:80]}" if record.get('detail') else ""
                    context_lines.append(
                        f"  [{record['action']}] {record['source']} -> {record['target']}{detail_part}"
                    )
    except Exception as e:
        print(f"    ⚠️ Graph context lookup failed: {e}")
        return ""
    
    if not context_lines:
        return ""
    
    # Deduplicate and limit
    context_lines = list(dict.fromkeys(context_lines))[:max_context_triples]
    
    return "EXISTING RELATIONSHIPS IN KNOWLEDGE GRAPH:\n" + "\n".join(context_lines)


def extract_triples_with_confidence(chunk, abbrev_table=None, neo4j_driver=None,
                                     n_samples=5, confidence_threshold=0.4, max_retries=2):
    """Extract triples using sampling-based confidence scoring (MedKGent style).
    
    Runs N parallel inferences per chunk with elevated temperature, counts
    frequency of each (action, target_citation) pair, and assigns
    confidence = frequency / N. Filters triples below threshold.
    
    Args:
        chunk: Source chunk dict
        abbrev_table: Abbreviation lookup table
        neo4j_driver: Optional Neo4j driver for graph-aware context
        n_samples: Number of inference runs (MedKGent uses 50, we use 5)
        confidence_threshold: Minimum confidence to keep (MedKGent uses 0.6)
        max_retries: Max retries per inference
    
    Returns:
        List of triples, each with added 'confidence' field.
    """
    from collections import Counter
    
    # Get graph context if Neo4j is available
    graph_context = graph_context_for_chunk(chunk, neo4j_driver)
    
    # Run N inferences with elevated temperature
    all_runs = []
    for i in range(n_samples):
        try:
            # Build prompt (same as extract_triples_ollama but with graph context)
            user_content = f"Extract all legal relationships from this text:\n\n"
            
            # Add graph context if available
            if graph_context:
                user_content += f"{graph_context}\n\n"
                user_content += "Use the above existing relationships to maintain consistency.\n\n"
            
            user_content += f"DOCUMENT ID: {chunk['id']}\n"
            user_content += f"TITLE: {chunk['doc_title']}\n"
            user_content += f"SOURCE TYPE: {chunk.get('source', 'unknown')}\n"
            
            if chunk.get('part'):
                user_content += f"PART: {chunk['part']}\n"
            if chunk.get('heading'):
                user_content += f"HEADING: {chunk['heading']}\n"
            if chunk.get('defined_terms'):
                user_content += f"DEFINED TERMS: {json.dumps(chunk['defined_terms'])}\n"
            if chunk.get('inline_amendments'):
                user_content += f"PRE-MARKED AMENDMENTS: {json.dumps(chunk['inline_amendments'][:5])}\n"
            
            user_content += f"\nTEXT:\n{chunk['content']}\n\n"
            user_content += "Respond with ONLY a JSON array of relationships. If none found, respond with []"
            
            response = ollama.chat(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                format="json",
                options={"temperature": 0.7, "num_predict": 2048, "num_ctx": 4096}  # Higher temp for diversity; 4096 ctx saves VRAM per slot
            )
            
            raw_text = response['message']['content'].strip()
            
            # Parse JSON
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if match:
                    parsed = json.loads(match.group())
                else:
                    continue
            
            # Handle wrapped formats
            if isinstance(parsed, dict):
                items = (parsed.get("mutations") or parsed.get("relationships")
                         or parsed.get("results") or parsed.get("data") or [])
                if not items and "action" in parsed:
                    items = [parsed]
            elif isinstance(parsed, list):
                items = parsed
            else:
                continue
            
            # Normalize and collect
            run_triples = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "action" not in item or "target_citation" not in item:
                    continue
                if not item["target_citation"]:
                    continue
                
                action = normalize_action(item["action"])
                if not action:
                    continue
                
                citation = normalize_citation(item["target_citation"], abbrev_table)
                if not citation or len(citation) < 3:
                    continue
                
                # Use (action, citation) as the key for frequency counting
                run_triples.append({
                    "action": action,
                    "target_citation": citation,
                    "detail_text": item.get("detail_text"),
                    "effective_date": item.get("effective_date"),
                })
            
            all_runs.append(run_triples)
            
        except Exception as e:
            print(f"    ⚠️ Confidence sample {i+1}/{n_samples} failed: {e}")
            continue
    
    if not all_runs:
        # Fallback to single-shot extraction
        print(f"    ⚠️ All confidence samples failed, falling back to single extraction")
        return extract_triples_ollama(chunk, abbrev_table, max_retries)
    
    # Count frequency of each (action, target_citation) pair
    triple_counts = Counter()
    triple_details = {}  # Store best detail_text and date for each key
    
    for run in all_runs:
        seen_in_run = set()  # Deduplicate within a single run
        for t in run:
            key = (t["action"], t["target_citation"])
            if key not in seen_in_run:
                triple_counts[key] += 1
                seen_in_run.add(key)
                # Keep detail_text from any run that provides it
                if key not in triple_details or (t.get("detail_text") and not triple_details[key].get("detail_text")):
                    triple_details[key] = t
    
    # Assign confidence = frequency / n_successful_runs and filter
    n_successful = len(all_runs)
    results = []
    
    source_prefix = chunk['id'].rsplit('.xml_', 1)[0] if '.xml_' in chunk['id'] else chunk['id']
    source_title = id_to_title.get(source_prefix, "")
    
    for key, count in triple_counts.items():
        confidence = count / n_successful
        
        if confidence < confidence_threshold:
            continue
        
        action, citation = key
        act_name = extract_act_name(citation)
        is_self = bool(source_title and act_name and source_title.lower() in act_name.lower())
        
        detail_info = triple_details.get(key, {})
        
        results.append({
            "action": action,
            "target_citation": citation,
            "target_act_name": act_name,
            "detail_text": detail_info.get("detail_text"),
            "effective_date": detail_info.get("effective_date"),
            "source_id": chunk['id'],
            "source_title": chunk.get('doc_title'),
            "source_section": chunk.get('section'),
            "in_force_date": chunk.get('in_force_date'),
            "extent": chunk.get('extent'),
            "is_self_amendment": is_self,
            "chunk_id": chunk['id'],
            "confidence": round(confidence, 2),
        })
    
    return results


################################################################
# CELL 7 — Extraction Function with Post-Processing
################################################################

def extract_triples_ollama(chunk, abbrev_table=None, max_retries=2):
    """Extract legal triples from a chunk using Ollama, with normalization."""

    # Build context-rich user prompt
    user_content = f"Extract all legal relationships from this text:\n\n"
    user_content += f"DOCUMENT ID: {chunk['id']}\n"
    user_content += f"TITLE: {chunk['doc_title']}\n"
    user_content += f"SOURCE TYPE: {chunk.get('source', 'unknown')}\n"

    if chunk.get('part'):
        user_content += f"PART: {chunk['part']}\n"
    if chunk.get('heading'):
        user_content += f"HEADING: {chunk['heading']}\n"
    if chunk.get('in_force_date'):
        user_content += f"IN FORCE DATE: {chunk['in_force_date']}\n"
    if chunk.get('extent'):
        user_content += f"EXTENT: {chunk['extent']}\n"
    if chunk.get('defined_terms'):
        user_content += f"DEFINED TERMS: {json.dumps(chunk['defined_terms'])}\n"
    if chunk.get('inline_amendments'):
        user_content += f"PRE-MARKED AMENDMENTS: {json.dumps(chunk['inline_amendments'][:5])}\n"

    user_content += f"\nTEXT:\n{chunk['content']}\n\n"
    user_content += "Respond with ONLY a JSON array of relationships. If none found, respond with []"

    for attempt in range(max_retries):
        try:
            response = ollama.chat(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                format="json",
                options={"temperature": 0.1, "num_predict": 2048, "num_ctx": 4096}  # 4096 ctx is plenty for ~500 token chunks
            )

            raw_text = response['message']['content'].strip()

            # Parse JSON
            try:
                parsed = json.loads(raw_text)
            except json.JSONDecodeError:
                match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if match:
                    parsed = json.loads(match.group())
                else:
                    print(f"    ⚠️ Could not parse JSON: {raw_text[:100]}...")
                    return []

            # Handle wrapped formats
            if isinstance(parsed, dict):
                items = (parsed.get("mutations") or parsed.get("relationships")
                         or parsed.get("results") or parsed.get("data") or [])
                if not items and "action" in parsed:
                    items = [parsed]
            elif isinstance(parsed, list):
                items = parsed
            else:
                return []

            # Validate, normalize, and clean each triple
            results = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "action" not in item or "target_citation" not in item:
                    continue
                if not item["target_citation"]:
                    continue

                # Normalize action
                action = normalize_action(item["action"])
                if not action:
                    print(f"    ⚠️ Unknown action '{item['action']}', skipping")
                    continue

                # Normalize citation
                citation = normalize_citation(item["target_citation"], abbrev_table)
                if not citation or len(citation) < 3:
                    continue

                # Extract parent act name
                act_name = extract_act_name(citation)

                # Detect self-amendment
                source_prefix = chunk['id'].rsplit('.xml_', 1)[0] if '.xml_' in chunk['id'] else chunk['id']
                source_title = id_to_title.get(source_prefix, "")
                is_self = bool(source_title and act_name and
                               source_title.lower() in act_name.lower())

                results.append({
                    "action": action,
                    "target_citation": citation,
                    "target_act_name": act_name,
                    "detail_text": item.get("detail_text"),
                    "effective_date": item.get("effective_date"),
                    "source_id": chunk['id'],
                    "source_title": chunk.get('doc_title'),
                    "source_section": chunk.get('section'),
                    "in_force_date": chunk.get('in_force_date'),
                    "extent": chunk.get('extent'),
                    "is_self_amendment": is_self,
                    "chunk_id": chunk['id'],  # link back to source chunk
                })

            return results

        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str or "connection" in error_str:
                print(f"    ⏳ Timeout (attempt {attempt+1}/{max_retries}), retrying...")
                time.sleep(2)
            else:
                print(f"    ❌ Error: {e}")
                return []

    print(f"    ❌ Failed after {max_retries} retries")
    return []


# Quick test
test_chunk = {
    "id": "test_act.xml_1",
    "doc_title": "Test Act 2024",
    "source": "legislation",
    "section": "1",
    "part": None,
    "heading": None,
    "extent": "E+W",
    "in_force_date": "2024-01-01",
    "defined_terms": None,
    "inline_amendments": None,
    "content": "Section 5 of the Welfare Reform Act 2012 is repealed."
}
test_result = extract_triples_ollama(test_chunk, abbrev_table)
print(f"🧪 Test extraction: {json.dumps(test_result, indent=2, default=str)}")
print("✅ Cell 7 done — Extraction with normalization working")


################################################################
# CELL 8 — Build Batch
################################################################

def build_smart_batch(corpus, batch_size=5000):
    """Pick diverse chunks: amendment-heavy + repeal-heavy + caselaw + general."""
    repeal_idxs = [i for i, c in enumerate(corpus)
                   if 'repeal' in c.get('content', '').lower() or 'omit' in c.get('content', '').lower()]
    amend_idxs = [i for i, c in enumerate(corpus)
                  if 'amend' in c.get('content', '').lower() or 'substitut' in c.get('content', '').lower()]
    case_idxs = [i for i, c in enumerate(corpus) if c.get('source') == 'judgment']
    leg_idxs = [i for i, c in enumerate(corpus) if c.get('source') == 'legislation']

    selected = set()
    for idx in repeal_idxs:
        selected.add(idx)
    for idx in amend_idxs:
        selected.add(idx)
    for idx in case_idxs:
        selected.add(idx)
    for idx in leg_idxs:
        if len(selected) >= batch_size:
            break
        selected.add(idx)

    batch_indices = sorted(list(selected))[:batch_size]

    batch_sources = defaultdict(int)
    for idx in batch_indices:
        s = corpus[idx].get('source', 'unknown')
        batch_sources[s] += 1
    print(f"📋 Batch: {len(batch_indices)} chunks | {dict(batch_sources)}")
    return batch_indices


batch_indices = build_smart_batch(smart_corpus, BATCH_SIZE)


################################################################
# CELL 9 — Run Extraction (PARALLEL — uses all GPUs)
################################################################

from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Load existing results
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        all_results = json.load(f)
    already_done = set(r['source_id'] for r in all_results if 'source_id' in r)
    print(f"📂 Loaded {len(all_results)} existing triples")
else:
    all_results = []
    already_done = set()

# Filter to chunks not yet processed
chunks_to_process = []
for idx in batch_indices:
    chunk = smart_corpus[idx]
    if chunk['id'] not in already_done:
        chunks_to_process.append(chunk)

NUM_WORKERS = 8   # match OLLAMA_NUM_PARALLEL=8 (set 1 if running without GPU)
SAVE_EVERY = 20   # save progress every N chunks

lock = threading.Lock()
stats = {"processed": 0, "triples_found": 0}
start_time = time.time()

print(f"\n🚀 Processing {len(chunks_to_process)} chunks with {MODEL_NAME} ({NUM_WORKERS} parallel workers)\n")


def process_chunk(chunk):
    """Extract triples from one chunk (thread-safe)."""
    triples = extract_triples_ollama(chunk, abbrev_table)
    return chunk, triples


with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
    futures = {executor.submit(process_chunk, c): c for c in chunks_to_process}

    for future in as_completed(futures):
        chunk, triples = future.result()

        with lock:
            stats["processed"] += 1
            if triples:
                for t in triples:
                    all_results.append(t)
                    stats["triples_found"] += 1

                self_count = sum(1 for t in triples if t.get('is_self_amendment'))
                print(f"[{stats['processed']}/{len(chunks_to_process)}] {chunk['id']}: "
                      f"{len(triples)} triples ({self_count} self-amend)")
            else:
                print(f"[{stats['processed']}/{len(chunks_to_process)}] {chunk['id']}: (none)")

            # Save periodically
            if stats['processed'] % SAVE_EVERY == 0:
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(all_results, f, indent=2, ensure_ascii=False)
                elapsed_so_far = time.time() - start_time
                speed = stats['processed'] / elapsed_so_far
                remaining = (len(chunks_to_process) - stats['processed']) / max(speed, 0.01)
                print(f"   💾 Saved ({len(all_results)} triples) | "
                      f"{speed:.1f} chunks/sec | ~{remaining:.0f}s remaining")

# Final save
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)

elapsed = time.time() - start_time
print(f"\n{'='*50}")
print(f"✅ DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
print(f"   Processed: {stats['processed']}")
print(f"   Triples found: {stats['triples_found']}")
print(f"   Total in file: {len(all_results)}")
print(f"   Speed: {stats['processed']/max(elapsed,1):.1f} chunks/sec")


################################################################
# CELL 10 — Post-Processing: Dedup & Quality Report
################################################################

with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
    all_triples = json.load(f)

print(f"📊 Post-Processing {len(all_triples)} triples...\n")

# 1. Deduplicate (same source_id + action + target_citation)
seen = set()
deduped = []
dup_count = 0
for t in all_triples:
    key = (t.get('source_id', ''), t.get('action', ''), t.get('target_citation', ''))
    if key not in seen:
        seen.add(key)
        deduped.append(t)
    else:
        dup_count += 1

print(f"   Duplicates removed: {dup_count}")

# 2. Re-normalize any remaining bad actions
fixed_actions = 0
for t in deduped:
    canonical = normalize_action(t.get('action', ''))
    if canonical and canonical != t.get('action'):
        t['action'] = canonical
        fixed_actions += 1
print(f"   Actions re-normalized: {fixed_actions}")

# 3. Re-normalize citations with abbreviation table
fixed_citations = 0
for t in deduped:
    old = t.get('target_citation', '')
    new = normalize_citation(old, abbrev_table)
    if new and new != old:
        t['target_citation'] = new
        t['target_act_name'] = extract_act_name(new)
        fixed_citations += 1
print(f"   Citations re-normalized: {fixed_citations}")

# Save cleaned version
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(deduped, f, indent=2, ensure_ascii=False)

print(f"\n✅ Cleaned: {len(deduped)} triples saved (was {len(all_triples)})")

# Quality report
print(f"\n{'='*50}")
print(f"📊 QUALITY REPORT")
print(f"{'='*50}")

actions = defaultdict(int)
for t in deduped:
    actions[t.get('action', 'UNKNOWN')] += 1
print(f"\n  Action distribution:")
for a, c in sorted(actions.items(), key=lambda x: -x[1]):
    marker = "✅" if a in CANONICAL_ACTIONS else "❌"
    print(f"    {marker} {a}: {c}")

self_amendments = sum(1 for t in deduped if t.get('is_self_amendment'))
print(f"\n  Self-amendments: {self_amendments}/{len(deduped)}")

with_date = sum(1 for t in deduped if t.get('effective_date'))
with_detail = sum(1 for t in deduped if t.get('detail_text'))
with_inforce = sum(1 for t in deduped if t.get('in_force_date'))
print(f"  With effective_date: {with_date}/{len(deduped)}")
print(f"  With detail_text: {with_detail}/{len(deduped)}")
print(f"  With in_force_date: {with_inforce}/{len(deduped)}")

vague = [t for t in deduped if t.get('target_citation') and len(t['target_citation']) < 15]
if vague:
    print(f"\n  ⚠️ Vague citations (<15 chars): {len(vague)}")
    for v in vague[:5]:
        print(f"      {v['source_id']} -> \"{v['target_citation']}\"")


################################################################
# CELL 11 — Results Analysis
################################################################

print(f"\n📝 Samples (one per action type):")
shown = set()
for t in deduped:
    if t['action'] not in shown:
        shown.add(t['action'])
        print(f"  [{t['action']}] {t.get('source_title','?')} s.{t.get('source_section','?')} -> {t['target_citation']}")
        if t.get('detail_text'):
            print(f"           detail: {t['detail_text'][:100]}...")


################################################################
# CELL 12 — Download (Colab)
################################################################

# Uncomment on Colab:
# from google.colab import files
# files.download(OUTPUT_FILE)
# if os.path.exists(SMART_CORPUS_FILE):
#     files.download(SMART_CORPUS_FILE)
# print("✅ Download started")


################################################################
# CELL 13 — Neo4j Ingestion (Improved Schema)
# Typed relationships + Chunk nodes
################################################################

from neo4j import GraphDatabase

NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "LegalPassword123"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print(f"✅ Connected to Neo4j at {NEO4J_URI}")

# Load data
with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
    triples = json.load(f)
with open(SMART_CORPUS_FILE, "r", encoding="utf-8") as f:
    corpus_chunks = json.load(f)
print(f"📂 Loaded {len(triples)} triples and {len(corpus_chunks)} chunks")

# Clear existing graph
with driver.session() as session:
    session.run("MATCH (n) DETACH DELETE n")
print("🗑️  Cleared existing graph")

# --- Step 1: Create Chunk nodes ---
print("\n📦 Creating Chunk nodes...")
chunk_count = 0
with driver.session() as session:
    for chunk in corpus_chunks:
        query = """
        MERGE (c:Chunk {id: $id})
        SET c.doc_title = $doc_title,
            c.source = $source,
            c.year = $year,
            c.section = $section,
            c.part = $part,
            c.heading = $heading,
            c.extent = $extent,
            c.in_force_date = $in_force_date,
            c.content = $content
        """
        session.run(query,
            id=chunk['id'],
            doc_title=chunk.get('doc_title'),
            source=chunk.get('source'),
            year=chunk.get('year'),
            section=chunk.get('section'),
            part=chunk.get('part'),
            heading=chunk.get('heading'),
            extent=chunk.get('extent'),
            in_force_date=chunk.get('in_force_date'),
            content=chunk.get('content', '')[:5000]  # Neo4j property size limit
        )
        chunk_count += 1
        if chunk_count % 500 == 0:
            print(f"   ... created {chunk_count} chunk nodes")
print(f"   ✅ Created {chunk_count} chunk nodes")

# --- Step 2: Create LegalDoc nodes + link chunks ---
print("\n📄 Creating LegalDoc nodes...")
with driver.session() as session:
    # Create LegalDoc for each source document (by prefix)
    for prefix, title in id_to_title.items():
        doc_type = "CaseLaw" if any(p in prefix for p in ["uksc_", "ewca_", "ewhc_", "ukut_"]) else "Legislation"
        year_match = re.search(r'(\d{4})', prefix)
        year = year_match.group(1) if year_match else None

        session.run("""
            MERGE (d:LegalDoc {id: $id})
            SET d.title = $title, d.type = $doc_type, d.year = $year
        """, id=prefix, title=title, doc_type=doc_type, year=year)

    # Link chunks to their parent LegalDoc
    session.run("""
        MATCH (c:Chunk), (d:LegalDoc)
        WHERE c.id STARTS WITH d.id + '.xml_'
        MERGE (c)-[:FROM_DOC]->(d)
    """)
print(f"   ✅ Created {len(id_to_title)} LegalDoc nodes with chunk links")

# --- Step 3: Create LEGAL_RELATIONSHIP edges with action_type property ---
# NOTE: Uses a single :LEGAL_RELATIONSHIP edge type (not typed edges like :AMENDS)
#       so that kg_query.py can query with: MATCH ()-[r:LEGAL_RELATIONSHIP]->()
#       The action is stored as r.action_type (e.g., 'AMENDS', 'REPEALS')
print("\n🔗 Creating LEGAL_RELATIONSHIP edges...")
loaded = 0
skipped = 0

with driver.session() as session:
    for t in triples:
        source_prefix = t.get('source_id', '').rsplit('.xml_', 1)[0] if '.xml_' in t.get('source_id', '') else t.get('source_id', '')
        target_citation = t.get('target_citation')
        action = t.get('action', 'CITES')

        if not target_citation:
            skipped += 1
            continue

        # Ensure action is canonical
        if action not in CANONICAL_ACTIONS:
            action = normalize_action(action) or 'CITES'

        target_act = t.get('target_act_name', target_citation)
        confidence = t.get('confidence', 1.0)  # Default 1.0 for legacy triples without confidence

        # Create source/target LegalDoc nodes and LEGAL_RELATIONSHIP edge
        query = """
        MERGE (s:LegalDoc {id: $source_id})
        SET s.title = $source_title, s.type = $source_type
        MERGE (t:LegalDoc {citation: $target_citation})
        SET t.act_name = $target_act
        MERGE (s)-[r:LEGAL_RELATIONSHIP {chunk_id: $chunk_id, action_type: $action_type}]->(t)
        SET r.detail = $detail,
            r.date = $date,
            r.in_force_date = $in_force_date,
            r.extent = $extent,
            r.is_self_amendment = $is_self,
            r.confidence = $confidence
        """

        source_type = "CaseLaw" if any(p in source_prefix for p in ["uksc_", "ewca_", "ewhc_"]) else "Legislation"

        session.run(query,
            source_id=source_prefix,
            source_title=t.get('source_title', source_prefix),
            source_type=source_type,
            target_citation=target_citation,
            target_act=target_act,
            action_type=action,
            chunk_id=t.get('chunk_id', ''),
            detail=t.get('detail_text'),
            date=t.get('effective_date'),
            in_force_date=t.get('in_force_date'),
            extent=t.get('extent'),
            is_self=t.get('is_self_amendment', False),
            confidence=confidence,
        )
        loaded += 1
        if loaded % 100 == 0:
            print(f"   ... loaded {loaded} relationships")

print(f"\n{'='*50}")
print(f"✅ NEO4J INGESTION COMPLETE")
print(f"   Loaded: {loaded} relationships")
print(f"   Skipped (null targets): {skipped}")

# --- Verify ---
with driver.session() as session:
    nodes = session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt").data()
    edges = session.run("""
        MATCH ()-[r]->()
        RETURN type(r) AS rel_type, count(r) AS cnt
        ORDER BY cnt DESC
    """).data()

print(f"\n📊 Graph Stats:")
for n in nodes:
    print(f"   {n['label']} nodes: {n['cnt']}")
print(f"\n   Relationship types:")
for e in edges:
    print(f"   {e['rel_type']}: {e['cnt']}")

driver.close()
print("\n✅ Neo4j connection closed")


################################################################
# CELL 14 — Interactive Visualization (PyVis)
################################################################

# !pip install pyvis networkx -q

from pyvis.network import Network

with open(OUTPUT_FILE, "r") as f:
    triples = json.load(f)

ACTION_COLORS = {
    "REPEALS": "#e74c3c",
    "AMENDS": "#f39c12",
    "SUBSTITUTES": "#9b59b6",
    "INSERTS": "#2ecc71",
    "COMMENCES": "#3498db",
    "REVOKES": "#e67e22",
    "OVERRULES": "#c0392b",
    "APPLIES": "#1abc9c",
    "CITES": "#95a5a6",
}

net = Network(
    height="700px", width="100%",
    bgcolor="#1a1a2e", font_color="#ffffff",
    directed=True, notebook=True, cdn_resources="remote"
)
net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=200, spring_strength=0.05)

source_nodes = set()
target_nodes = set()
for t in triples:
    source_prefix = t["source_id"].rsplit('.xml_', 1)[0] if '.xml_' in t["source_id"] else t["source_id"]
    source_nodes.add((source_prefix, t.get("source_title", source_prefix)))
    if t.get("target_citation"):
        target_nodes.add(t["target_citation"])

for node_id, title in source_nodes:
    label = title[:40] + "..." if len(title) > 40 else title
    net.add_node(node_id, label=label, color="#3498db", size=20, shape="dot", title=f"Source: {title}")

for node in target_nodes:
    act = extract_act_name(node) or node
    label = act[:40] + "..." if len(act) > 40 else act
    net.add_node(node, label=label, color="#f1c40f", size=15, shape="dot", title=f"Target: {node}")

for t in triples:
    if not t.get("target_citation"):
        continue
    source_prefix = t["source_id"].rsplit('.xml_', 1)[0] if '.xml_' in t["source_id"] else t["source_id"]
    color = ACTION_COLORS.get(t["action"], "#ffffff")
    tooltip = t["action"]
    if t.get("effective_date"):
        tooltip += f" | {t['effective_date']}"
    net.add_edge(source_prefix, t["target_citation"], label=t["action"], color=color, width=2, title=tooltip, arrows="to")

net.show("kg_visualization.html")
print(f"✅ Visualization saved: {len(source_nodes)} sources, {len(target_nodes)} targets, {len(triples)} edges")

# On Colab: display(HTML("kg_visualization.html"))
