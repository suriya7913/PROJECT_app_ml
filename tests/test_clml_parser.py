"""
Unit tests for CLMLParser — Hierarchical CLML XML Parser.
Uses real XML files from data/raw_legislation/.
"""

import os
import sys
import unittest

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.xml_parser import CLMLParser, parse_legislation_xml


FINANCE_ACT = os.path.join(PROJECT_ROOT, "data", "raw_legislation", "ukpga_2024_3.xml")
TRANSPORT_ACT = os.path.join(PROJECT_ROOT, "data", "raw_legislation", "ukpga_1980_34.xml")


@unittest.skipUnless(os.path.exists(FINANCE_ACT), "Finance Act XML not available")
class TestFinanceAct2024(unittest.TestCase):
    """Test CLMLParser against Finance Act 2024 (ukpga_2024_3.xml)."""

    @classmethod
    def setUpClass(cls):
        cls.chunks = parse_legislation_xml(FINANCE_ACT)
        cls.chunk_map = {c['section_number']: c for c in cls.chunks}

    def test_produces_chunks(self):
        """Parser should produce a reasonable number of chunks."""
        self.assertGreater(len(self.chunks), 50)

    def test_hierarchy_capture(self):
        """Section 1 should have Part/Chapter/Crossheading hierarchy."""
        s1 = self.chunk_map.get('1')
        self.assertIsNotNone(s1, "Section 1 should exist")
        h = s1['hierarchy']
        self.assertIn('part', h)
        self.assertIn('Part 1', h['part'])
        self.assertIn('chapter', h)
        self.assertIn('Chapter 1', h['chapter'])
        self.assertIn('crossheading', h)

    def test_commentary_lookup(self):
        """Commentary IDs should be resolved to affecting act info."""
        parser = CLMLParser(FINANCE_ACT)
        parser._extract_doc_metadata()
        parser._build_commentary_lookup()

        self.assertGreater(len(parser.commentary_lookup), 0,
                          "Should find at least one commentary")

        # Check that at least some commentaries have non-empty affecting_act
        with_act = [v for v in parser.commentary_lookup.values()
                    if v.get('affecting_act')]
        self.assertGreater(len(with_act), 0,
                          "Some commentaries should have affecting_act")
        # Verify a commentary has the right structure
        sample = with_act[0]
        self.assertIn('type', sample)
        self.assertIn('text', sample)
        self.assertIn('affecting_act', sample)

    def test_block_amendment_trapping(self):
        """Section 12 should have contains_block_amendment=True
        and BlockAmendment text should NOT appear in vector_text."""
        s12 = self.chunk_map.get('12')
        self.assertIsNotNone(s12, "Section 12 should exist")

        ge = s12['graph_edges']
        self.assertTrue(ge['contains_block_amendment'],
                       "Section 12 should contain a block amendment")
        self.assertIn('block_amendment_text', ge,
                     "block_amendment_text should be set")
        self.assertGreater(len(ge['block_amendment_text']), 50,
                          "Block amendment text should be substantial")

        # The block amendment text starts with "Part 2 Corporation tax..."
        # This should NOT be in vector_text
        self.assertNotIn("Corporation tax", s12['vector_text'],
                        "BlockAmendment text should not leak into vector_text")

    def test_subsection_extraction(self):
        """Section 12 should have exactly 3 subsections (not duplicated)."""
        s12 = self.chunk_map.get('12')
        self.assertIsNotNone(s12)

        subs = s12['graph_edges']['has_subsection']
        self.assertEqual(subs, ['12(1)', '12(2)', '12(3)'],
                        f"Expected 3 clean subsections, got {subs}")

    def test_output_schema(self):
        """All chunks should have the required fields."""
        required_fields = [
            'chunk_id', 'source', 'doc_title', 'year',
            'section_number', 'hierarchy', 'vector_text', 'graph_edges'
        ]
        for chunk in self.chunks[:20]:
            for field in required_fields:
                self.assertIn(field, chunk,
                            f"Missing field '{field}' in chunk {chunk.get('chunk_id')}")
            self.assertIsInstance(chunk['hierarchy'], dict)
            self.assertIsInstance(chunk['graph_edges'], dict)
            self.assertTrue(len(chunk['vector_text']) >= 20,
                          f"vector_text too short in chunk {chunk.get('chunk_id')}")

    def test_context_isolation(self):
        """Hierarchy context from Part 1 should not bleed into Part 2."""
        part1_chunks = [c for c in self.chunks
                        if c.get('hierarchy', {}).get('part', '').startswith('Part 1')]
        part2_chunks = [c for c in self.chunks
                        if c.get('hierarchy', {}).get('part', '').startswith('Part 2')]

        self.assertGreater(len(part1_chunks), 0, "Should have Part 1 chunks")
        self.assertGreater(len(part2_chunks), 0, "Should have Part 2 chunks")

        # Part 2 chunks should NOT have Chapter 1 from Part 1
        for c in part2_chunks:
            ch = c['hierarchy'].get('chapter', '')
            if ch:
                self.assertNotIn('Chapter 1', ch,
                               f"Part 2 chunk {c['chunk_id']} has Part 1 chapter bleed")


@unittest.skipUnless(os.path.exists(TRANSPORT_ACT), "Transport Act XML not available")
class TestTransportAct1980(unittest.TestCase):
    """Test CLMLParser against Transport Act 1980 (ukpga_1980_34.xml)."""

    @classmethod
    def setUpClass(cls):
        cls.chunks = parse_legislation_xml(TRANSPORT_ACT)

    def test_produces_chunks(self):
        self.assertGreater(len(self.chunks), 10)

    def test_defined_terms(self):
        """Should extract defined terms like 'the Corporation'."""
        terms_found = False
        for chunk in self.chunks:
            if chunk.get('defined_terms'):
                terms_found = True
                self.assertIsInstance(chunk['defined_terms'], dict)
                break
        self.assertTrue(terms_found, "Should find defined terms in Transport Act")

    def test_hierarchy_has_part(self):
        """Chunks should have Part in their hierarchy."""
        with_part = [c for c in self.chunks
                     if c.get('hierarchy', {}).get('part')]
        self.assertGreater(len(with_part), 0,
                          "Some chunks should have 'part' in hierarchy")


if __name__ == '__main__':
    unittest.main()


# ═══════════════════════════════════════════════════
# Effects / Amendments Parser Tests
# ═══════════════════════════════════════════════════

from utils.xml_parser import (
    parse_effects_xml, load_effects_triples,
    _ref_to_section_id, _extract_section_refs,
)

TRANSPORT_EFFECTS = os.path.join(PROJECT_ROOT, "data", "amendments", "ukpga_1980_34_effects.xml")
FINANCE_EFFECTS = os.path.join(PROJECT_ROOT, "data", "amendments", "ukpga_2024_3_effects.xml")


class TestRefToSectionId(unittest.TestCase):
    """Test _ref_to_section_id helper."""

    def test_simple_section(self):
        self.assertEqual(_ref_to_section_id('section-47'), '47')

    def test_section_with_letter(self):
        self.assertEqual(_ref_to_section_id('section-52A-13'), '52A')

    def test_section_subsection_stripped(self):
        self.assertEqual(_ref_to_section_id('section-46-1'), '46')

    def test_schedule_paragraph(self):
        self.assertEqual(_ref_to_section_id('schedule-6-paragraph-10'), 'Schedule 6_10')

    def test_bare_schedule(self):
        self.assertEqual(_ref_to_section_id('schedule-4'), 'Schedule 4')

    def test_part_returns_none(self):
        self.assertIsNone(_ref_to_section_id('part-III'))

    def test_article(self):
        self.assertEqual(_ref_to_section_id('article-2'), 'article_2')

    def test_empty_returns_none(self):
        self.assertIsNone(_ref_to_section_id(''))
        self.assertIsNone(_ref_to_section_id(None))


@unittest.skipUnless(os.path.exists(TRANSPORT_EFFECTS), "Transport Act effects XML not available")
class TestTransportEffects(unittest.TestCase):
    """Test parse_effects_xml against Transport Act 1980 effects."""

    @classmethod
    def setUpClass(cls):
        cls.effects = parse_effects_xml(TRANSPORT_EFFECTS)

    def test_produces_effects(self):
        self.assertGreater(len(self.effects), 10)

    def test_output_schema(self):
        required = [
            'effect_id', 'action', 'target_act_name', 'target_citation',
            'target_chunk_ids', 'source_title', 'source_chunk_ids',
            'confidence', 'provenance',
        ]
        for e in self.effects:
            for field in required:
                self.assertIn(field, e, f"Missing field '{field}'")

    def test_target_chunk_ids_format(self):
        """Target chunk IDs should follow ukpga_YYYY_NN.xml_SECTION pattern."""
        for e in self.effects:
            for cid in e['target_chunk_ids']:
                self.assertTrue(cid.startswith('ukpga_1980_34.xml_'),
                              f"Bad target chunk ID: {cid}")

    def test_repeals_have_chunk_ids(self):
        """REPEALS effects should have target chunk IDs."""
        repeals = [e for e in self.effects if e['action'] == 'REPEALS']
        self.assertGreater(len(repeals), 0)
        for r in repeals:
            self.assertGreater(len(r['target_chunk_ids']), 0,
                             f"REPEALS effect {r['effect_id']} missing target_chunk_ids")

    def test_confidence_is_one(self):
        for e in self.effects:
            self.assertEqual(e['confidence'], 1.0)

    def test_provenance(self):
        for e in self.effects:
            self.assertEqual(e['provenance'], 'effects_api')


@unittest.skipUnless(
    os.path.exists(os.path.join(PROJECT_ROOT, "data", "amendments")),
    "Amendments directory not available"
)
class TestLoadAllEffects(unittest.TestCase):
    """Integration test for load_effects_triples across all files."""

    @classmethod
    def setUpClass(cls):
        cls.all_effects = load_effects_triples()

    def test_produces_effects(self):
        self.assertGreater(len(self.all_effects), 100)

    def test_high_chunk_id_coverage(self):
        """At least 90% of effects should have target_chunk_ids."""
        with_ids = sum(1 for e in self.all_effects if e['target_chunk_ids'])
        ratio = with_ids / len(self.all_effects)
        self.assertGreater(ratio, 0.90,
                          f"Only {ratio:.1%} of effects have target_chunk_ids")


if __name__ == '__main__':
    unittest.main()
