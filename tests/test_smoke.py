"""Smoke tests for all 6 Chemistry 2e MCP server tools."""

from __future__ import annotations

import re
import unittest

from textbook_mcp.server import (
    browse_chapter,
    get_exercise_bundle,
    lookup_reference_data,
    read_section,
    search_exercises,
    search_textbook,
)


class SmokeTestTools(unittest.TestCase):
    def test_browse_chapter_toc(self) -> None:
        res = browse_chapter(0)
        self.assertIsInstance(res, str)
        self.assertIn("OpenStax Chemistry 2e — Table of Contents", res)
        chapters = re.findall(r"^- \*\*Ch \d+\*\*:", res, flags=re.MULTILINE)
        appendices = re.findall(r"^- \*\*app-[A-Z]\*\*:", res, flags=re.MULTILINE)
        self.assertEqual(len(chapters), 21)
        self.assertEqual(len(appendices), 13)

    def test_browse_chapter(self) -> None:
        res = browse_chapter(5)
        self.assertIsInstance(res, str)
        self.assertIn("# Chapter 5:", res)
        sections = re.findall(r"^- \*\*(?:5\.\d+|5\.0):", res, flags=re.MULTILINE)
        self.assertEqual(len(sections), 4)
        self.assertIn("Exercises: 86 (43 with official key, 43 unkeyed)", res)
        self.assertIn("(48 exercises)", res)
        self.assertIn("read_section", res)
        self.assertRegex(res, r"Figures: [1-9]\d*")
        self.assertRegex(res, r"Tables: \d+")
        self.assertRegex(res, r"Examples: \d+")
        self.assertNotIn("By the end of this section, you will be able to:", res)
        self.assertIn("Define energy", res)

    def test_search_textbook(self) -> None:
        res = search_textbook("Hess law enthalpy", limit=2)
        self.assertIsInstance(res, str)
        self.assertIn("section_id=5.3", res)
        self.assertIn("hess", res.lower())
        if "[excerpt;" in res:
            self.assertIn("read_section(", res)
            self.assertNotIn("use pagination or narrower query", res)

    def test_search_textbook_natural_language_fallback(self) -> None:
        res = search_textbook("Explain the technique of calorimetry", limit=3)
        self.assertIsInstance(res, str)
        first_hit = re.search(r"section_id=([0-9]+\.[0-9]+|app-[A-Z])", res)
        self.assertIsNotNone(first_hit)
        assert first_hit is not None
        self.assertEqual(first_hit.group(1), "5.2")

    def test_read_section(self) -> None:
        res = read_section("5.3", chunk_index=0, max_chunks=1)
        self.assertIsInstance(res, str)
        self.assertIn("# Section 5.3:", res)
        self.assertIn("chunk_index=1", res)
        self.assertGreater(len(res), 200)
        self.assertIn("## Figures", res)
        self.assertIn("media/", res)
        self.assertIn("Alt:", res)

    def test_read_section_invalid_id_suggests_closest(self) -> None:
        res = read_section("Section 5.3")
        self.assertIsInstance(res, str)
        self.assertTrue(res.startswith("Error:"))
        self.assertIn("Closest valid section_id matches:", res)
        self.assertIn("`5.3`", res)

    def test_search_exercises(self) -> None:
        res = search_exercises(query="enthalpy", chapter=5, answer_filter="with_answer", limit=3)
        self.assertIsInstance(res, str)
        self.assertIn("verified_official_key", res)
        self.assertNotIn("key: unavailable", res)
        self.assertNotIn("Official Answer Key:", res)

    def test_search_exercises_pagination(self) -> None:
        page1 = search_exercises(section_id="5.3", limit=15, offset=0)
        self.assertIn("showing 1-15 of 48", page1)
        self.assertIn("offset=15", page1)

        page2 = search_exercises(section_id="5.3", limit=15, offset=15)
        self.assertIn("showing 16-30 of 48", page2)
        self.assertIn("offset=30", page2)

        ids1 = set(re.findall(r"^### (Exercise \d+\.\d+)", page1, flags=re.MULTILINE))
        ids2 = set(re.findall(r"^### (Exercise \d+\.\d+)", page2, flags=re.MULTILINE))
        self.assertEqual(len(ids1), 15)
        self.assertEqual(len(ids2), 15)
        self.assertTrue(ids1.isdisjoint(ids2))

    def test_get_exercise_bundle_hides_answers_by_default(self) -> None:
        res = get_exercise_bundle(["1.1", "1.2"])
        self.assertIsInstance(res, str)
        self.assertIn("include_answers=False", res)
        self.assertIn("Exercise 1.1", res)
        self.assertIn("Exercise 1.2", res)
        self.assertIn("key: verified_official_key", res)
        self.assertIn("key: unavailable", res)
        self.assertNotIn("Official Answer Key:", res)

    def test_get_exercise_bundle_with_answers(self) -> None:
        res = get_exercise_bundle(["1.1", "1.2"], include_answers=True)
        self.assertIsInstance(res, str)
        self.assertIn("include_answers=True", res)
        blocks = res.split("### Exercise ")
        self.assertEqual(len(blocks), 3)
        odd_block, even_block = blocks[1], blocks[2]
        self.assertIn("**Official Answer Key:**", odd_block)
        self.assertNotIn("**Official Answer Key:** unavailable", odd_block)
        self.assertIn("**Official Answer Key:** unavailable", even_block)

    def test_lookup_reference_data(self) -> None:
        res = lookup_reference_data("NH3", category="thermodynamics")
        self.assertIsInstance(res, str)
        self.assertIn("NH₃", res)

    def test_lookup_reference_data_property_and_compound_aliases(self) -> None:
        res = lookup_reference_data("entropy water", limit=1)
        self.assertIsInstance(res, str)
        self.assertIn("app-G", res)
        self.assertIn("H₂O(l)", res)

        red = lookup_reference_data("reduction potential silver", limit=1)
        self.assertIsInstance(red, str)
        self.assertIn("app-L", red)
        self.assertIn("Ag⁺", red)


if __name__ == "__main__":
    unittest.main()
