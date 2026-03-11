"""
LegalKGent — XML Parsing Utilities
====================================
All XML parsers for legislation, case law, effects, and explanatory notes.
Extracted from 2_kg_creation.py to enable reuse and independent testing.
"""

import os
import re
import xml.etree.ElementTree as ET
from config import (
    LEG_NS, META_NS, DC_NS, AKN_NS, TNA_NS,
    RAW_LEGISLATION_DIR, RAW_CASELAW_DIR, RAW_SI_DIR,
    AMENDMENTS_DIR, NOTES_DIR,
)


# ─────────────────────────────────────────────
# NAMESPACE HELPERS
# ─────────────────────────────────────────────

def _leg(tag):
    """Prefix a tag with the legislation namespace."""
    return f'{{{LEG_NS}}}{tag}'


def _akn(tag):
    """Prefix a tag with the AKN namespace."""
    return f'{{{AKN_NS}}}{tag}'


def _meta(tag):
    """Prefix a tag with the metadata namespace."""
    return f'{{{META_NS}}}{tag}'


# ─────────────────────────────────────────────
# TEXT EXTRACTION HELPERS
# ─────────────────────────────────────────────

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
    """Find all <Term> definitions in the XML: 'the Act' -> full name."""
    terms = {}
    for term_elem in root.iter(_leg('Term')):
        term_text = extract_text_recursive(term_elem)
        if term_text:
            parent = None
            for p in root.iter():
                if term_elem in list(p):
                    parent = p
                    break
            if parent is not None:
                parent_text = extract_text_recursive(parent)
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
    """Walk up the tree to find the nearest <Pblock>/<Part> Title ancestor."""
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


# ─────────────────────────────────────────────
# PARSER: PRIMARY LEGISLATION (CLML)
# ─────────────────────────────────────────────

def parse_legislation_xml(filepath: str) -> list[dict]:
    """Parse a CLML legislation XML file into smart chunks."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    ⚠️ XML parse error: {filepath}: {e}")
        return []

    filename = os.path.basename(filepath).replace('.xml', '')

    # Extract document title
    doc_title = filename
    prelim_title = root.find(f'.//{_leg("PrimaryPrelims")}/{_leg("Title")}')
    if prelim_title is not None:
        doc_title = extract_text_recursive(prelim_title)
    else:
        title_elem = root.find(f'.//{_leg("Title")}')
        if title_elem is not None:
            doc_title = extract_text_recursive(title_elem)

    # Extract year
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

    # Parse sections (P1, P1group)
    for section_elem in root.iter():
        tag_local = section_elem.tag.split('}')[-1] if '}' in section_elem.tag else section_elem.tag
        if tag_local not in ('P1', 'P1group'):
            continue

        pnum_elem = section_elem.find(f'.//{_leg("Pnumber")}')
        section_num = extract_text_recursive(pnum_elem) if pnum_elem is not None else None
        id_attr = section_elem.get('id', '')

        if section_num:
            section_id = section_num
        elif id_attr:
            section_id = id_attr.replace('section-', '').replace('schedule-', 'SCHEDULE ')
        else:
            continue

        chunk_id = f"{filename}.xml_{section_id}"
        content = extract_text_recursive(section_elem)
        if not content or len(content) < 20:
            continue

        heading = None
        title_elem = section_elem.find(_leg('Title'))
        if title_elem is not None:
            heading = extract_text_recursive(title_elem)

        extent = section_elem.get('RestrictExtent', None)
        in_force_date = section_elem.get('RestrictStartDate', None)
        part_title = get_parent_pblock_title(section_elem, root)
        internal_refs = extract_internal_links(section_elem)
        inline_amendments = extract_inline_amendments(section_elem)

        content_prefix = f"ACT: {doc_title} ({year}) | SECTION: {section_id}"
        if part_title:
            content_prefix += f" | PART: {part_title}"
        if heading:
            content_prefix += f" | HEADING: {heading}"
        content_prefix += " | TEXT: "

        chunks.append({
            "id": chunk_id,
            "source": "legislation",
            "doc_title": doc_title,
            "year": year,
            "section": str(section_id),
            "part": part_title,
            "heading": heading,
            "extent": extent,
            "in_force_date": in_force_date or enactment_date,
            "internal_refs": internal_refs or None,
            "inline_amendments": inline_amendments or None,
            "defined_terms": defined_terms or None,
            "content": content_prefix + content
        })

    # Parse Schedules
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

            chunks.append({
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
                "defined_terms": defined_terms or None,
                "content": f"ACT: {doc_title} ({year}) | SECTION: {para_id} | TEXT: {content}"
            })

    return chunks


# ─────────────────────────────────────────────
# PARSER: CASE LAW (AKN)
# ─────────────────────────────────────────────

def parse_caselaw_xml(filepath: str) -> list[dict]:
    """Parse a case law XML file into chunks (AKN namespace)."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    ⚠️ Case law parse error: {filepath}: {e}")
        return []

    filename = os.path.basename(filepath).replace('.xml', '')

    # Extract case name
    case_name = None
    dc_title = root.find(f'.//{{{DC_NS}}}title')
    if dc_title is not None:
        case_name = extract_text_recursive(dc_title)
    if not case_name:
        frbr_name = root.find(f'.//{_akn("FRBRname")}')
        if frbr_name is not None:
            case_name = frbr_name.get('value', None) or extract_text_recursive(frbr_name)
    if not case_name:
        case_name = filename

    # Extract year
    year_match = re.search(r'(\d{4})', filename)
    year = year_match.group(1) if year_match else "unknown"

    # Determine court level
    court_level = "Unknown"
    if "uksc_" in filename:
        court_level = "Supreme Court"
    elif "ewca_" in filename:
        court_level = "Court of Appeal"
    elif "ewhc_" in filename:
        court_level = "High Court"

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


# ─────────────────────────────────────────────
# PARSER: EFFECTS / AMENDMENTS (Atom Feed)
# ─────────────────────────────────────────────

ATOM_NS = "http://www.w3.org/2005/Atom"

def parse_effects_xml(filepath: str) -> list[dict]:
    """
    Parse an effects/amendments XML feed into ground-truth triples.
    The effects feed from legislation.gov.uk contains structured data about
    which provisions modify which other provisions — this is API-verified,
    not LLM-extracted.

    XML structure (Atom feed with ukm: namespace):
      <entry>
        <content>
          <ukm:Effect Type="words substituted" AffectedProvisions="s. 5(1)" ...>
            <ukm:AffectedTitle>Transport Act 2000</ukm:AffectedTitle>
            <ukm:AffectingTitle>Railways Act 2005</ukm:AffectingTitle>
            <ukm:InForceDates>
              <ukm:InForce Date="2024-01-01" .../>
            </ukm:InForceDates>
          </ukm:Effect>
        </content>
      </entry>

    Returns list of dicts with: source, action, target, detail, confidence=1.0
    """
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    ⚠️ Effects XML parse error: {filepath}: {e}")
        return []

    triples = []

    # Find <ukm:Effect> elements (metadata namespace)
    for effect in root.iter(_meta('Effect')):
        effect_type = effect.get('Type', '')

        # Map effect type to our canonical actions
        action = _map_effect_type(effect_type)
        if not action:
            continue

        # Titles are CHILD ELEMENTS, not attributes
        affected_title_elem = effect.find(_meta('AffectedTitle'))
        affecting_title_elem = effect.find(_meta('AffectingTitle'))

        affected_title = extract_text_recursive(affected_title_elem) if affected_title_elem is not None else ''
        affecting_title = extract_text_recursive(affecting_title_elem) if affecting_title_elem is not None else ''

        if not affected_title or not affecting_title:
            continue

        # Provisions are in ATTRIBUTES on the <ukm:Effect> element
        affected_provisions = effect.get('AffectedProvisions', '')
        affecting_provisions = effect.get('AffectingProvisions', '')

        # Build citations
        target_citation = affected_title
        if affected_provisions:
            target_citation += f" {affected_provisions}"
        source_citation = affecting_title
        if affecting_provisions:
            source_citation += f" {affecting_provisions}"

        # Extract in-force date from <ukm:InForceDates>/<ukm:InForce Date="..."/>
        effective_date = None
        in_force_dates = effect.find(_meta('InForceDates'))
        if in_force_dates is not None:
            in_force = in_force_dates.find(_meta('InForce'))
            if in_force is not None:
                effective_date = in_force.get('Date')

        triples.append({
            "source_id": f"effects_{affecting_title}",
            "source_title": affecting_title,
            "source_section": affecting_provisions,
            "action": action,
            "target_citation": target_citation.strip(),
            "target_act_name": affected_title,
            "detail_text": f"{effect_type} by {source_citation}",
            "effective_date": effective_date,
            "confidence": 1.0,  # Ground truth from API
            "provenance": "effects_api",
        })

    return triples


def _map_effect_type(effect_type: str) -> str | None:
    """Map legislation.gov.uk effect types to our canonical actions."""
    mapping = {
        # Direct matches
        "inserted":     "INSERTS",
        "substituted":  "SUBSTITUTES",
        "repealed":     "REPEALS",
        "amended":      "AMENDS",
        "applied":      "APPLIES",
        "commenced":    "COMMENCES",
        "revoked":      "REVOKES",
        "extended":     "EXTENDS",
        # Partial matches
        "words substituted":       "SUBSTITUTES",
        "text amended":            "AMENDS",
        "words inserted":          "INSERTS",
        "words repealed":          "REPEALS",
        "coming into force":       "COMMENCES",
        "s. substituted":          "SUBSTITUTES",
        "power to modify":         "EMPOWERS",
        "applied (with modifications)": "APPLIES",
        "restricted":              "PROHIBITS",
    }

    lower_type = effect_type.lower().strip()
    # Check exact match first
    if lower_type in mapping:
        return mapping[lower_type]
    # Partial match
    for key, action in mapping.items():
        if key in lower_type:
            return action
    return None


# ─────────────────────────────────────────────
# PARSER: EXPLANATORY NOTES
# ─────────────────────────────────────────────

def parse_notes_xml(filepath: str) -> dict:
    """
    Parse explanatory notes XML and return a section->commentary mapping.
    Notes provide section-by-section commentary explaining what each provision does.

    Returns: dict mapping section_id -> notes_text
    """
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"    ⚠️ Notes XML parse error: {filepath}: {e}")
        return {}

    notes_map = {}

    # Try ExplanatoryNotes/Body structure
    for comment_elem in root.iter(_leg('Comment')):
        # Comments typically reference a section
        section_ref = comment_elem.get('Type', '')
        comment_text = extract_text_recursive(comment_elem)
        if comment_text and len(comment_text) > 20:
            notes_map[section_ref] = comment_text

    # Try Commentary elements (more common in CLML)
    for commentary in root.iter(_leg('Commentary')):
        ref = commentary.get('id', '')
        text = extract_text_recursive(commentary)
        if text and len(text) > 20:
            notes_map[ref] = text

    # Also try plain paragraph structure
    for para in root.iter(_leg('P')):
        text = extract_text_recursive(para)
        if text and len(text) > 30:
            # Use parent section reference if available
            parent = None
            for p in root.iter():
                if para in list(p):
                    parent = p
                    break
            if parent is not None:
                section_ref = parent.get('id', '') or parent.get('about', '')
                if section_ref:
                    existing = notes_map.get(section_ref, "")
                    notes_map[section_ref] = (existing + " " + text).strip()

    return notes_map


# ─────────────────────────────────────────────
# CORPUS BUILDER — combines all parsers
# ─────────────────────────────────────────────

def build_smart_corpus(
    legislation_dir: str = RAW_LEGISLATION_DIR,
    si_dir: str = RAW_SI_DIR,
    caselaw_dir: str = RAW_CASELAW_DIR,
    notes_dir: str = NOTES_DIR,
) -> list[dict]:
    """
    Parse all raw XML files into smart chunks with CLML metadata.
    Optionally enriches chunks with explanatory notes.
    """
    all_chunks = []

    # 1. Parse primary legislation
    if os.path.exists(legislation_dir):
        xml_files = sorted(f for f in os.listdir(legislation_dir) if f.endswith('.xml'))
        print(f"📂 Found {len(xml_files)} legislation XML files")
        for f in xml_files:
            chunks = parse_legislation_xml(os.path.join(legislation_dir, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    else:
        print(f"⚠️ No legislation directory: {legislation_dir}")

    # 2. Parse statutory instruments (same CLML format)
    if os.path.exists(si_dir):
        xml_files = sorted(f for f in os.listdir(si_dir) if f.endswith('.xml'))
        print(f"📂 Found {len(xml_files)} statutory instrument XML files")
        for f in xml_files:
            chunks = parse_legislation_xml(os.path.join(si_dir, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    else:
        print(f"ℹ️  No SI directory: {si_dir}")

    # 3. Parse case law
    if os.path.exists(caselaw_dir):
        xml_files = sorted(f for f in os.listdir(caselaw_dir) if f.endswith('.xml'))
        print(f"📂 Found {len(xml_files)} case law XML files")
        for f in xml_files:
            chunks = parse_caselaw_xml(os.path.join(caselaw_dir, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    else:
        print(f"⚠️ No case law directory: {caselaw_dir}")

    # 4. Enrich with explanatory notes (if available)
    if os.path.exists(notes_dir):
        notes_files = sorted(f for f in os.listdir(notes_dir) if f.endswith('.xml'))
        print(f"📝 Found {len(notes_files)} explanatory notes files")
        # Build a mapping: filename_prefix -> notes_map
        all_notes = {}
        for f in notes_files:
            notes_map = parse_notes_xml(os.path.join(notes_dir, f))
            if notes_map:
                prefix = f.replace('.xml', '').replace('_notes', '').replace('_ExplanatoryNotes', '')
                all_notes[prefix] = notes_map
                print(f"   {f}: {len(notes_map)} note sections")

        # Attach notes to matching chunks
        enriched = 0
        for chunk in all_chunks:
            chunk_prefix = chunk['id'].rsplit('.xml_', 1)[0] if '.xml_' in chunk['id'] else chunk['id']
            notes_map = all_notes.get(chunk_prefix, {})
            if notes_map:
                # Try matching by section number
                section = chunk.get('section', '')
                for note_key, note_text in notes_map.items():
                    if section and section in note_key:
                        chunk['notes_text'] = note_text[:500]
                        enriched += 1
                        break

        if enriched:
            print(f"   ✅ Enriched {enriched} chunks with explanatory notes")
    else:
        print(f"ℹ️  No notes directory: {notes_dir}")

    print(f"\n✅ Smart corpus built: {len(all_chunks)} chunks")
    return all_chunks


def load_effects_triples(amendments_dir: str = AMENDMENTS_DIR) -> list[dict]:
    """Load all ground-truth effects triples from amendments directory."""
    all_effects = []
    if not os.path.exists(amendments_dir):
        print(f"ℹ️  No amendments directory: {amendments_dir}")
        return all_effects

    xml_files = sorted(f for f in os.listdir(amendments_dir) if f.endswith('.xml'))
    print(f"🔗 Found {len(xml_files)} effects XML files")
    for f in xml_files:
        triples = parse_effects_xml(os.path.join(amendments_dir, f))
        all_effects.extend(triples)
        if triples:
            print(f"   {f}: {len(triples)} effects")

    print(f"   ✅ Total effects triples: {len(all_effects)}")
    return all_effects
