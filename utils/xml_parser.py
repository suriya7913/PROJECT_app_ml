"""
LegalKGent — XML Parsing Utilities
====================================
All XML parsers for legislation, case law, effects, and explanatory notes.
Contains CLMLParser (class-based recursive walker) for hierarchical CLML parsing.
"""

import os
import re
import xml.etree.ElementTree as ET
from config import (
    LEG_NS, META_NS, DC_NS, AKN_NS,
    RAW_LEGISLATION_DIR,
    RAW_CASELAW_DIR,
    RAW_SI_DIR,
    AMENDMENTS_DIR,
    NAMESPACES
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


def _strip_ns(tag):
    """Strip namespace from a tag, returning the local name."""
    return tag.split('}')[-1] if '}' in tag else tag


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


def _extract_own_text(elem):
    """
    Extract text from an element and its children,
    but SKIP <BlockAmendment> subtrees entirely.
    This gives us the section's 'own' text without foreign legislation.
    """
    parts = []
    if elem.text:
        parts.append(elem.text.strip())
    for child in elem:
        if _strip_ns(child.tag) == 'BlockAmendment':
            # Skip the block amendment content, but keep any tail text
            if child.tail:
                parts.append(child.tail.strip())
        else:
            parts.append(_extract_own_text(child))
            if child.tail:
                parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)


# ─────────────────────────────────────────────
# CLMLParser — CLASS-BASED RECURSIVE WALKER
# ─────────────────────────────────────────────

class CLMLParser:
    """
    Recursive CLML parser that walks UK Legislation XML sequentially,
    maintaining hierarchical context, preventing text duplication,
    and capturing explicit relationships for a Knowledge Graph.
    """

    STRUCTURAL_TAGS = {'Part', 'Chapter', 'Pblock'}
    SECTION_TAGS = {'P1group'}
    SKIP_TAGS = {'BlockAmendment'}

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.filename = os.path.basename(filepath).replace('.xml', '')
        self.tree = ET.parse(filepath)
        self.root = self.tree.getroot()
        self.chunks = []
        self.commentary_lookup = {}   # id -> {text, affecting_act, type}
        self.doc_title = self.filename
        self.year = "unknown"
        self.enactment_date = None
        self.defined_terms = {}

    # ── Step 1: Extract document-level metadata ──

    def _extract_doc_metadata(self):
        """Extract title, year, enactment date, and defined terms."""
        root = self.root

        # Title
        prelim_title = root.find(f'.//{_leg("PrimaryPrelims")}/{_leg("Title")}')
        if prelim_title is not None:
            self.doc_title = extract_text_recursive(prelim_title)
        else:
            title_elem = root.find(f'.//{_leg("Title")}')
            if title_elem is not None:
                self.doc_title = extract_text_recursive(title_elem)

        # Year
        number_elem = root.find(f'.//{_leg("Number")}')
        year_text = extract_text_recursive(number_elem) if number_elem is not None else self.filename
        year_match = re.search(r'(\d{4})', year_text)
        self.year = year_match.group(1) if year_match else "unknown"

        # Enactment date
        date_elem = root.find(f'.//{_leg("DateOfEnactment")}/{_leg("DateText")}')
        if date_elem is not None:
            date_text = extract_text_recursive(date_elem)
            date_match = re.search(r'(\d{1,2})\w*\s+(\w+)\s+(\d{4})', date_text)
            if date_match:
                try:
                    from datetime import datetime
                    self.enactment_date = datetime.strptime(
                        f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}",
                        "%d %B %Y"
                    ).strftime("%Y-%m-%d")
                except ValueError:
                    pass

        # Defined terms
        self.defined_terms = self._extract_defined_terms()

    def _extract_defined_terms(self):
        """Find all <Term> definitions: short name -> full Act name."""
        terms = {}
        for term_elem in self.root.iter(_leg('Term')):
            term_id = term_elem.get('id', '')
            term_text = extract_text_recursive(term_elem)
            if not term_text:
                continue

            # Try the id-based approach first (e.g., id="term-the-corporation")
            if term_id:
                # Find the parent context for the full definition
                parent = None
                for p in self.root.iter():
                    if term_elem in list(p):
                        parent = p
                        break
                if parent is not None:
                    parent_text = extract_text_recursive(parent)
                    # Pattern: "short name" means ...
                    # Store the term with its definition context
                    terms[term_text] = parent_text[:300]
                continue

            # Fallback: Act-abbreviation pattern
            parent = None
            for p in self.root.iter():
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

    # ── Step 2: Build commentary lookup table ──

    def _build_commentary_lookup(self):
        """
        Pre-scan <Commentaries> block to build a lookup:
          commentary_id -> {type, text, affecting_act, affecting_uri}
        """
        for commentary in self.root.iter(_leg('Commentary')):
            cid = commentary.get('id', '')
            ctype = commentary.get('Type', '')
            if not cid:
                continue

            text = extract_text_recursive(commentary)

            # Extract affecting act from <Citation> child
            # Citations can be in legislation NS or metadata NS
            affecting_act = ''
            affecting_uri = ''
            for citation in commentary.iter(_leg('Citation')):
                affecting_act = citation.get('Title', '') or extract_text_recursive(citation)
                affecting_uri = citation.get('URI', '')
                break  # Take the first citation
            # Fallback: check metadata namespace
            if not affecting_act:
                for citation in commentary.iter(_meta('Citation')):
                    affecting_act = citation.get('Title', '') or extract_text_recursive(citation)
                    affecting_uri = citation.get('URI', '')
                    break

            self.commentary_lookup[cid] = {
                'type': ctype,
                'text': text[:500],
                'affecting_act': affecting_act,
                'affecting_uri': affecting_uri,
            }

    # ── Helper: iterate skipping BlockAmendment subtrees ──

    def _iter_skip_ba(self, node):
        """Iterate descendants of node, skipping BlockAmendment subtrees."""
        for child in node:
            if _strip_ns(child.tag) == 'BlockAmendment':
                continue
            yield child
            yield from self._iter_skip_ba(child)

    # ── Step 3: Recursive walker engine ──

    def _walk_tree(self, node, context: dict):
        """
        Recursively walk the XML tree, maintaining hierarchical context.
        CRITICAL: context is cloned at each level to prevent sibling bleed.
        """
        current_context = context.copy()
        # Deep-copy the hierarchy dict to prevent mutation
        current_context['hierarchy'] = context['hierarchy'].copy()

        # Inherit RestrictStartDate / RestrictExtent if present on this node
        if node.get('RestrictStartDate'):
            current_context['in_force_date'] = node.get('RestrictStartDate')
        if node.get('RestrictExtent'):
            current_context['extent'] = node.get('RestrictExtent')

        tag = _strip_ns(node.tag)

        # Step 4: Structural hierarchy — update context, don't yield chunks
        if tag in self.STRUCTURAL_TAGS:
            self._handle_structure(node, tag, current_context)
            return

        # Step 5: BlockAmendment — halt recursion for this branch
        if tag in self.SKIP_TAGS:
            return

        # Step 6: Section processing — yield chunks
        if tag in self.SECTION_TAGS:
            self._handle_p1group(node, current_context)
            return

        # For all other tags, continue walking children
        for child in node:
            self._walk_tree(child, current_context)

    # ── Step 4: Structural hierarchy handlers ──

    def _handle_structure(self, node, tag, context):
        """
        Handle Part, Chapter, Pblock elements.
        Extract their Number+Title, update context hierarchy, then recurse.
        """
        # Extract number
        number_elem = node.find(_leg('Number'))
        number_text = extract_text_recursive(number_elem) if number_elem is not None else ''

        # Extract title
        title_elem = node.find(_leg('Title'))
        title_text = extract_text_recursive(title_elem) if title_elem is not None else ''

        # Build label: e.g., "Part 1: Income tax and corporation tax"
        if number_text and title_text:
            label = f"{number_text}: {title_text}"
        elif number_text:
            label = number_text
        elif title_text:
            label = title_text
        else:
            label = tag

        # Map tag to hierarchy key
        hierarchy_key = tag.lower()
        if hierarchy_key == 'pblock':
            hierarchy_key = 'crossheading'

        context['hierarchy'][hierarchy_key] = label

        # Continue walking children
        for child in node:
            self._walk_tree(child, context)

    # ── Step 6: P1group processing ──

    def _handle_p1group(self, p1group_node, context):
        """
        Handle <P1group>: extract heading from <Title>,
        find child <P1> elements, and process each.
        """
        # Extract heading from P1group's <Title>
        title_elem = p1group_node.find(_leg('Title'))
        heading = extract_text_recursive(title_elem) if title_elem is not None else None

        # Clean up repealed headings (dots only)
        if heading and re.match(r'^[\s.]+$', heading):
            heading = "[Repealed]"

        context['heading'] = heading

        # Inherit RestrictStartDate / RestrictExtent from P1group
        if p1group_node.get('RestrictStartDate'):
            context['in_force_date'] = p1group_node.get('RestrictStartDate')
        if p1group_node.get('RestrictExtent'):
            context['extent'] = p1group_node.get('RestrictExtent')

        # Find and process all <P1> children (usually one, but can be multiple)
        for p1 in p1group_node:
            if _strip_ns(p1.tag) == 'P1':
                self._process_section(p1, context)

    # ── Step 7: Section text extraction & graph edge generation ──

    def _process_section(self, p1_node, context):
        """
        Process a <P1> element: extract section number, text, subsections,
        commentary references, block amendments, and internal links.
        """
        # Extract section number from <Pnumber>
        pnum_elem = p1_node.find(_leg('Pnumber'))
        if pnum_elem is not None:
            section_number = extract_text_recursive(pnum_elem)
        else:
            section_number = p1_node.get('id', '').replace('section-', '')

        if not section_number:
            return

        # Extract vector_text — own text EXCLUDING BlockAmendment content
        vector_text = _extract_own_text(p1_node)
        if not vector_text or len(vector_text) < 20:
            return

        # Extract subsections (P2 only — skip those inside BlockAmendment)
        subsections = []
        for child in self._iter_skip_ba(p1_node):
            tag = _strip_ns(child.tag)
            if tag == 'P2':
                pnum = child.find(_leg('Pnumber'))
                sub_num = extract_text_recursive(pnum) if pnum is not None else ""
                if sub_num:
                    subsections.append(f"{section_number}({sub_num})")

        # Resolve CommentaryRef tags (from own text only)
        amended_by = []
        seen_refs = set()
        for cref in self._iter_skip_ba(p1_node):
            if _strip_ns(cref.tag) == 'CommentaryRef':
                ref_id = cref.get('Ref', '')
                if ref_id and ref_id in self.commentary_lookup and ref_id not in seen_refs:
                    seen_refs.add(ref_id)
                    entry = self.commentary_lookup[ref_id]
                    amended_by.append({
                        'commentary_id': ref_id,
                        'affecting_act': entry['affecting_act'],
                        'type': entry['type'],
                        'text': entry['text'],
                    })

        # Detect and extract BlockAmendment content
        has_block_amendment = False
        block_amendment_text = ""
        for child in p1_node.iter():
            if _strip_ns(child.tag) == 'BlockAmendment':
                has_block_amendment = True
                ba_text = extract_text_recursive(child)
                if ba_text:
                    block_amendment_text += ba_text[:1000] + " "
        block_amendment_text = block_amendment_text.strip()[:2000]

        # Extract internal links (skip BlockAmendment)
        internal_refs = []
        for elem in self._iter_skip_ba(p1_node):
            if _strip_ns(elem.tag) == 'InternalLink':
                ref_text = extract_text_recursive(elem)
                if ref_text:
                    internal_refs.append(ref_text)

        # Extract inline amendments (skip BlockAmendment)
        inline_amendments = []
        for elem in self._iter_skip_ba(p1_node):
            if _strip_ns(elem.tag) == 'InlineAmendment':
                amend_text = extract_text_recursive(elem)
                if amend_text:
                    inline_amendments.append(amend_text)

        # Determine in_force_date (inherit from context or node attributes)
        in_force_date = (
            p1_node.get('RestrictStartDate')
            or context.get('in_force_date')
            or self.enactment_date
        )
        extent = (
            p1_node.get('RestrictExtent')
            or context.get('extent')
        )

        # Build the chunk
        chunk_id = f"{self.filename}.xml_{section_number}"

        # Build graph_edges
        graph_edges = {
            'has_subsection': subsections if subsections else [],
            'contains_block_amendment': has_block_amendment,
            'internal_refs': internal_refs if internal_refs else [],
            'inline_amendments': inline_amendments if inline_amendments else [],
            'amended_by': amended_by if amended_by else [],
        }
        if block_amendment_text:
            graph_edges['block_amendment_text'] = block_amendment_text

        self.chunks.append({
            'chunk_id': chunk_id,
            'source': 'legislation',
            'doc_title': self.doc_title,
            'year': self.year,
            'section_number': str(section_number),
            'heading': context.get('heading'),
            'hierarchy': context['hierarchy'].copy(),
            'in_force_date': in_force_date,
            'extent': extent,
            'enactment_date': self.enactment_date,
            'defined_terms': self.defined_terms if self.defined_terms else None,
            'vector_text': vector_text,
            'graph_edges': graph_edges,
        })

    # ── Step 8: Schedule processing ──

    def _process_schedules(self):
        """Parse <Schedule> elements into chunks, preserving hierarchy."""
        for schedule in self.root.iter(_leg('Schedule')):
            sched_num_elem = schedule.find(_leg('Number'))
            sched_num = extract_text_recursive(sched_num_elem) if sched_num_elem is not None else "SCHEDULE"

            sched_title_elem = schedule.find(_leg('Title'))
            sched_title = extract_text_recursive(sched_title_elem) if sched_title_elem is not None else ""

            sched_label = f"{sched_num}: {sched_title}" if sched_title else sched_num

            for para in schedule.iter(_leg('P1')):
                pnum = para.find(f'.//{_leg("Pnumber")}')
                para_num = extract_text_recursive(pnum) if pnum is not None else ""
                para_id = f"{sched_num}" + (f"_{para_num}" if para_num else "")

                vector_text = _extract_own_text(para)
                if not vector_text or len(vector_text) < 20:
                    continue

                # Subsections
                subsections = []
                for child in para.iter():
                    if _strip_ns(child.tag) == 'P2':
                        sub_pnum = child.find(_leg('Pnumber'))
                        sub_num = extract_text_recursive(sub_pnum) if sub_pnum is not None else ""
                        if sub_num:
                            subsections.append(f"{para_num}({sub_num})")

                # Block amendments
                has_ba = False
                ba_text = ""
                for child in para.iter():
                    if _strip_ns(child.tag) == 'BlockAmendment':
                        has_ba = True
                        ba = extract_text_recursive(child)
                        if ba:
                            ba_text += ba[:1000] + " "
                ba_text = ba_text.strip()[:2000]

                # Commentary refs
                amended_by = []
                for cref in para.iter(_leg('CommentaryRef')):
                    ref_id = cref.get('Ref', '')
                    if ref_id and ref_id in self.commentary_lookup:
                        entry = self.commentary_lookup[ref_id]
                        amended_by.append({
                            'commentary_id': ref_id,
                            'affecting_act': entry['affecting_act'],
                            'type': entry['type'],
                            'text': entry['text'],
                        })

                extent = para.get('RestrictExtent', schedule.get('RestrictExtent'))
                in_force = para.get('RestrictStartDate', schedule.get('RestrictStartDate'))

                graph_edges = {
                    'has_subsection': subsections,
                    'contains_block_amendment': has_ba,
                    'internal_refs': [extract_text_recursive(l) for l in para.iter(_leg('InternalLink')) if extract_text_recursive(l)],
                    'inline_amendments': [extract_text_recursive(a) for a in para.iter(_leg('InlineAmendment')) if extract_text_recursive(a)],
                    'amended_by': amended_by,
                }
                if ba_text:
                    graph_edges['block_amendment_text'] = ba_text

                self.chunks.append({
                    'chunk_id': f"{self.filename}.xml_{para_id}",
                    'source': 'legislation',
                    'doc_title': self.doc_title,
                    'year': self.year,
                    'section_number': para_id,
                    'heading': None,
                    'hierarchy': {'schedule': sched_label},
                    'in_force_date': in_force or self.enactment_date,
                    'extent': extent,
                    'enactment_date': self.enactment_date,
                    'defined_terms': self.defined_terms if self.defined_terms else None,
                    'vector_text': vector_text,
                    'graph_edges': graph_edges,
                })

    # ── Main entry point ──

    def parse(self) -> list[dict]:
        """Run the full parse: metadata → commentaries → tree walk → schedules."""
        self._extract_doc_metadata()
        self._build_commentary_lookup()

        # Initial context for the recursive walker
        initial_context = {
            'hierarchy': {},
            'in_force_date': self.root.get('RestrictStartDate'),
            'extent': self.root.get('RestrictExtent'),
            'heading': None,
        }

        # Walk the body of the legislation
        body = self.root.find(f'.//{_leg("Body")}')
        if body is not None:
            self._walk_tree(body, initial_context)

        # Also walk Primary (for acts without explicit body)
        if not self.chunks:
            primary = self.root.find(f'.//{_leg("Primary")}')
            if primary is not None:
                self._walk_tree(primary, initial_context)

        # Process schedules separately
        self._process_schedules()

        return self.chunks


def parse_legislation_xml(filepath: str) -> list[dict]:
    """Parse a CLML legislation XML file into smart hierarchical chunks."""
    try:
        parser = CLMLParser(filepath)
        return parser.parse()
    except ET.ParseError as e:
        print(f"     XML parse error: {filepath}: {e}")
        return []


# ─────────────────────────────────────────────
# PARSER: CASE LAW (AKN)
# ─────────────────────────────────────────────

def parse_caselaw_xml(filepath: str) -> list[dict]:
    """Parse a case law XML file into chunks (AKN namespace)."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"     Case law parse error: {filepath}: {e}")
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

    # Extract Neutral Citation
    neutral_citation = None
    uk_cite = root.find(f'.//{{https://caselaw.nationalarchives.gov.uk/akn}}cite')
    if uk_cite is not None:
        neutral_citation = extract_text_recursive(uk_cite)
        if neutral_citation:
            case_name = f"{case_name} {neutral_citation}" if case_name != filename else neutral_citation
            
    # Extract year
    year_match = re.search(r'(\d{4})', filename)
    year = year_match.group(1) if year_match else "unknown"

    # Determine court level
    court_level = "Unknown"
    if "uksc_" in filename or "ukpc_" in filename:
        court_level = "Supreme Court / Privy Council"
    elif "ewca_" in filename or "ewcr_" in filename:
        court_level = "Court of Appeal"
    elif "ewhc_" in filename:
        court_level = "High Court"
    elif "ukftt_" in filename or "ukut_" in filename:
        court_level = "Tribunal"

    chunks = []
    paragraphs = list(root.iter(_akn('paragraph'))) or list(root.iter(_akn('Paragraph')))

    # If no structured paragraphs, extract all text as one chunk
    if not paragraphs:
        full_text = extract_text_recursive(root)
        if full_text and len(full_text.strip()) > 50:
            chunks.append({
                "chunk_id": f"{filename}.xml_full",
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
                "content": f"CASE: {case_name} ({year}) | COURT: {court_level} | TEXT: {full_text.strip()}"
            })
        return chunks

    for i, para in enumerate(paragraphs):
        para_text = extract_text_recursive(para)
        if not para_text or len(para_text.strip()) < 30:
            continue

        # Prefer explicit <num> tag for paragraph number in AKN 3.0
        num_elem = para.find(_akn('num'))
        if num_elem is not None and extract_text_recursive(num_elem):
            para_num = extract_text_recursive(num_elem).strip().strip('.')
        else:
            para_num = para.get('Number', para.get('eId', str(i + 1)))

        chunks.append({
            "chunk_id": f"{filename}.xml_{para_num}",
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
            "content": f"CASE: {case_name} ({year}) | COURT: {court_level} | PARA: {para_num} | TEXT: {para_text.strip()}"
        })

    return chunks


# ─────────────────────────────────────────────
# PARSER: EFFECTS / AMENDMENTS (Atom Feed)
# ─────────────────────────────────────────────

ATOM_NS = "http://www.w3.org/2005/Atom"

# Map legislation.gov.uk class names to our filename prefixes
_CLASS_TO_PREFIX = {
    'UnitedKingdomPublicGeneralAct':        'ukpga',
    'UnitedKingdomLocalAct':                'ukla',
    'UnitedKingdomStatutoryInstrument':      'uksi',
    'ScottishAct':                          'asp',
    'WelshParliamentAct':                   'asc',
    'NorthernIrelandAct':                   'nia',
    'NorthernIrelandOrderInCouncil':        'nisi',
    'UnitedKingdomMinisterialOrder':        'ukmo',
    'UnitedKingdomChurchInstrument':        'ukci',
    'ScottishStatutoryInstrument':          'ssi',
    'WelshStatutoryInstrument':             'wsi',
}


def _ref_to_section_id(ref: str) -> str | None:
    """
    Convert a ukm:Section Ref value to a section-level ID matching our chunk_ids.

    Examples:
        'section-52A-13'        → '52A'     (subsection stripped)
        'section-46-1'          → '46'      (subsection stripped)
        'section-47'            → '47'
        'schedule-6-paragraph-10'→ 'Schedule 6_10'
        'schedule-4'            → 'Schedule 4'
        'part-III'              → None      (part-level, not a chunk)
        'article-2'             → 'article_2'
    """
    if not ref:
        return None

    # Schedule references: schedule-6-paragraph-4-3 → Schedule 6_4
    m = re.match(r'schedule-(\d+)-paragraph-(\w+)', ref)
    if m:
        return f"Schedule {m.group(1)}_{m.group(2)}"

    # Bare schedule: schedule-4 → Schedule 4
    m = re.match(r'schedule-(\d+)$', ref)
    if m:
        return f"Schedule {m.group(1)}"

    # Section references: section-52A-13 → 52A
    m = re.match(r'section-(\d+[A-Z]?)', ref)
    if m:
        return m.group(1)

    # Article references (SIs): article-2 → article_2
    m = re.match(r'article-(\d+)', ref)
    if m:
        return f"article_{m.group(1)}"

    # Part-level refs are not chunks, skip
    if ref.startswith('part-'):
        return None

    return None


def _extract_section_refs(provisions_elem) -> list[dict]:
    """
    Extract all <ukm:Section> and <ukm:SectionRange> references from
    a provisions element.

    Returns list of dicts: [{'ref': ..., 'uri': ..., 'text': ...}, ...]
    """
    refs = []
    if provisions_elem is None:
        return refs

    # Single sections
    for sec in provisions_elem.iter(_meta('Section')):
        refs.append({
            'ref': sec.get('Ref', ''),
            'uri': sec.get('URI', ''),
            'text': extract_text_recursive(sec) or '',
        })

    # Section ranges (e.g. s. 46(1)-(3))
    for sr in provisions_elem.iter(_meta('SectionRange')):
        refs.append({
            'ref': sr.get('Start', ''),
            'uri': sr.get('URI', ''),
            'text': extract_text_recursive(sr) or '',
            'range_end': sr.get('End', ''),
        })

    return refs


def parse_effects_xml(filepath: str) -> list[dict]:
    """
    Parse an effects/amendments XML feed into high-fidelity ground-truth triples.

    Extracts structured <ukm:Section Ref="..."/> references and maps them
    to deterministic chunk IDs (matching CLMLParser output), enabling precise
    Knowledge Graph edge creation.

    Returns list of dicts with enriched fields including target_chunk_ids.
    """
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"     Effects XML parse error: {filepath}: {e}")
        return []

    triples = []

    for effect in root.iter(_meta('Effect')):
        effect_type = effect.get('Type', '')
        effect_id = effect.get('EffectId', '')

        # Map effect type to our canonical actions
        action = _map_effect_type(effect_type)
        if not action:
            continue

        # ── Titles (child elements) ──
        affected_title_elem = effect.find(_meta('AffectedTitle'))
        affecting_title_elem = effect.find(_meta('AffectingTitle'))

        affected_title = extract_text_recursive(affected_title_elem) if affected_title_elem is not None else ''
        affecting_title = extract_text_recursive(affecting_title_elem) if affecting_title_elem is not None else ''

        if not affected_title or not affecting_title:
            continue

        # ── Build base filename for target chunk IDs ──
        affected_class = effect.get('AffectedClass', '')
        affected_year = effect.get('AffectedYear', '')
        affected_number = effect.get('AffectedNumber', '')
        prefix = _CLASS_TO_PREFIX.get(affected_class, 'ukpga')
        base_filename = f"{prefix}_{affected_year}_{affected_number}"

        # ── Extract structured section references ──
        affected_prov_elem = effect.find(_meta('AffectedProvisions'))
        affecting_prov_elem = effect.find(_meta('AffectingProvisions'))

        affected_refs = _extract_section_refs(affected_prov_elem)
        affecting_refs = _extract_section_refs(affecting_prov_elem)

        # Map refs to chunk IDs
        target_chunk_ids = []
        for ref_info in affected_refs:
            sec_id = _ref_to_section_id(ref_info['ref'])
            if sec_id:
                chunk_id = f"{base_filename}.xml_{sec_id}"
                if chunk_id not in target_chunk_ids:
                    target_chunk_ids.append(chunk_id)

        # Build source chunk IDs (from affecting act)
        affecting_class = effect.get('AffectingClass', '')
        affecting_year = effect.get('AffectingYear', '')
        affecting_number = effect.get('AffectingNumber', '')
        affecting_prefix = _CLASS_TO_PREFIX.get(affecting_class, 'ukpga')
        affecting_base = f"{affecting_prefix}_{affecting_year}_{affecting_number}"

        source_chunk_ids = []
        for ref_info in affecting_refs:
            sec_id = _ref_to_section_id(ref_info['ref'])
            if sec_id:
                chunk_id = f"{affecting_base}.xml_{sec_id}"
                if chunk_id not in source_chunk_ids:
                    source_chunk_ids.append(chunk_id)

        # ── Fallback text provisions (from attributes) ──
        affected_provisions = effect.get('AffectedProvisions', '')
        affecting_provisions = effect.get('AffectingProvisions', '')

        # Build human-readable citations
        target_citation = affected_title
        if affected_provisions:
            target_citation += f" {affected_provisions}"
        source_citation = affecting_title
        if affecting_provisions:
            source_citation += f" {affecting_provisions}"

        # ── Extract in-force dates ──
        effective_date = None
        is_prospective = False
        in_force_dates = effect.find(_meta('InForceDates'))
        if in_force_dates is not None:
            for in_force in in_force_dates.iter(_meta('InForce')):
                date = in_force.get('Date')
                if date:
                    effective_date = date
                    break
                if in_force.get('Prospective') == 'true':
                    is_prospective = True

        triples.append({
            "effect_id": effect_id,
            "action": action,
            "effect_type_raw": effect_type,

            # Target (affected legislation)
            "target_act_name": affected_title,
            "target_citation": target_citation.strip(),
            "target_chunk_ids": target_chunk_ids,
            "affected_uri": effect.get('AffectedURI', ''),

            # Source (affecting legislation)
            "source_title": affecting_title,
            "source_section": affecting_provisions,
            "source_chunk_ids": source_chunk_ids,
            "affecting_uri": effect.get('AffectingURI', ''),

            # Temporal
            "effective_date": effective_date,
            "is_prospective": is_prospective,
            "applied": effect.get('Applied', '') == 'true',

            # Metadata
            "confidence": 1.0,
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
# CORPUS BUILDER — combines all parsers
# ─────────────────────────────────────────────

def build_smart_corpus(
    legislation_dir: str = RAW_LEGISLATION_DIR,
    si_dir: str = RAW_SI_DIR,
    caselaw_dir: str = RAW_CASELAW_DIR,
    types: list[str] | None = None,
) -> list[dict]:
    """
    Parse all raw XML files into smart chunks with CLML metadata.
    """
    all_chunks = []
    if types is None:
        types = ['legislation', 'si', 'caselaw']

    # 1. Parse primary legislation
    if 'legislation' in types and os.path.exists(legislation_dir):
        
        xml_files = sorted(f for f in os.listdir(legislation_dir) if f.endswith('.xml'))
        print(f"Found {len(xml_files)} legislation XML files")
        for f in xml_files:
            chunks = parse_legislation_xml(os.path.join(legislation_dir, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    elif 'legislation' in types:
        print(f"No legislation directory: {legislation_dir}")

    # 2. Parse statutory instruments (same CLML format)
    if 'si' in types and os.path.exists(si_dir):
        xml_files = sorted(f for f in os.listdir(si_dir) if f.endswith('.xml'))
        print(f"Found {len(xml_files)} statutory instrument XML files")
        for f in xml_files:
            chunks = parse_legislation_xml(os.path.join(si_dir, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    elif 'si' in types:
        print(f"ℹNo SI directory: {si_dir}")

    # 3. Parse case law
    if 'caselaw' in types and os.path.exists(caselaw_dir):
        xml_files = sorted(f for f in os.listdir(caselaw_dir) if f.endswith('.xml'))
        print(f"Found {len(xml_files)} case law XML files")
        for f in xml_files:
            chunks = parse_caselaw_xml(os.path.join(caselaw_dir, f))
            all_chunks.extend(chunks)
            print(f"   {f}: {len(chunks)} chunks")
    elif 'caselaw' in types:
        print(f"No case law directory: {caselaw_dir}")



    print(f"\nSmart corpus built: {len(all_chunks)} chunks")
    return all_chunks


def load_effects_triples(amendments_dir: str = AMENDMENTS_DIR) -> list[dict]:
    """Load all ground-truth effects triples from amendments directory."""
    all_effects = []
    if not os.path.exists(amendments_dir):
        print(f"No amendments directory: {amendments_dir}")
        return all_effects

    xml_files = sorted(f for f in os.listdir(amendments_dir) if f.endswith('.xml'))
    print(f" Found {len(xml_files)} effects XML files")
    for f in xml_files:
        triples = parse_effects_xml(os.path.join(amendments_dir, f))
        all_effects.extend(triples)
        if triples:
            print(f"   {f}: {len(triples)} effects")

    print(f"  Total effects triples: {len(all_effects)}")
    return all_effects
