"""Smoke tests for all 7 Chemistry 2e MCP server tools."""

from __future__ import annotations

import unittest

from textbook_mcp.server import (
    browse_chapter,
    get_exercise_bundle,
    get_toc,
    lookup_reference_data,
    read_section,
    search_exercises,
    search_textbook,
)


class SmokeTestTools(unittest.TestCase):
    def test_get_toc(self) -> None:
        res = get_toc()
        self.assertEqual(len(res["chapters"]), 21)
        self.assertEqual(len(res["appendices"]), 13)

    def test_browse_chapter(self) -> None:
        res = browse_chapter(5)
        self.assertEqual(res["chapter"], 5)
        self.assertEqual(len(res["sections"]), 4)
        self.assertEqual(res["exercises_summary"]["total"], 86)
        self.assertEqual(res["exercises_summary"]["with_official_answer_key"], 43)
        self.assertNotIn("figures", res)
        self.assertGreater(res["figures_summary"]["total"], 0)
        self.assertIn("read_section", res["figures_summary"]["hint"])
        sec_5_1 = next(s for s in res["sections"] if s["id"] == "5.1")
        self.assertGreater(len(sec_5_1["learning_objectives"]), 400)

    def test_search_textbook(self) -> None:
        res = search_textbook("Hess law enthalpy", limit=2)
        self.assertGreaterEqual(res["count"], 1)
        top = res["results"][0]
        self.assertEqual(top["section_id"], "5.3")
        self.assertIn("truncated", top)
        self.assertIn("hess", top["snippet"].lower())
        if top["truncated"]:
            self.assertIn("read_section(", top["snippet"])
            self.assertNotIn("use pagination or narrower query", top["snippet"])

    def test_search_textbook_natural_language_fallback(self) -> None:
        res = search_textbook("Explain the technique of calorimetry", limit=3)
        self.assertGreaterEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["section_id"], "5.2")

    def test_read_section(self) -> None:
        res = read_section("5.3", chunk_index=0, max_chunks=1)
        self.assertEqual(res["citation"], "Section 5.3")
        self.assertTrue(res["has_more"])
        self.assertEqual(res["next_chunk_index"], 1)
        self.assertGreater(len(res["content"]), 100)
        if res["figures_in_section"]:
            self.assertIn("alt_text", res["figures_in_section"][0])

    def test_read_section_invalid_id_suggests_closest(self) -> None:
        res = read_section("Section 5.3")
        self.assertIn("error", res)
        self.assertIn("closest_matches", res)
        suggested_ids = [m["section_id"] for m in res["closest_matches"]]
        self.assertIn("5.3", suggested_ids)

    def test_search_exercises(self) -> None:
        res = search_exercises(query="enthalpy", chapter=5, answer_filter="with_answer", limit=3)
        self.assertGreaterEqual(res["count"], 1)
        for ex in res["exercises"]:
            self.assertTrue(ex["has_answer"])
            self.assertEqual(ex["answer_status"], "verified_official_key")
            self.assertNotIn("answer_key", ex)

    def test_get_exercise_bundle_hides_answers_by_default(self) -> None:
        res = get_exercise_bundle(["1.1", "1.2"])
        self.assertFalse(res["include_answers"])
        self.assertEqual(len(res["exercises"]), 2)
        odd, even = res["exercises"]
        self.assertTrue(odd["has_answer"])
        self.assertEqual(odd["answer_status"], "verified_official_key")
        self.assertNotIn("answer_key", odd)
        self.assertFalse(even["has_answer"])
        self.assertEqual(even["answer_status"], "unavailable")
        self.assertNotIn("answer_key", even)

    def test_get_exercise_bundle_with_answers(self) -> None:
        res = get_exercise_bundle(["1.1", "1.2"], include_answers=True)
        self.assertTrue(res["include_answers"])
        self.assertEqual(len(res["exercises"]), 2)
        odd, even = res["exercises"]
        self.assertTrue(odd["has_answer"])
        self.assertIn("answer_key", odd)
        self.assertNotEqual(odd["answer_key"], "unavailable")
        self.assertFalse(even["has_answer"])
        self.assertEqual(even["answer_key"], "unavailable")

    def test_lookup_reference_data(self) -> None:
        res = lookup_reference_data("NH3", category="thermodynamics")
        self.assertGreaterEqual(res["count"], 1)
        self.assertIn("NH₃", res["results"][0]["content"])

    def test_lookup_reference_data_property_and_compound_aliases(self) -> None:
        res = lookup_reference_data("entropy water", limit=1)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["appendix_id"], "app-G")
        self.assertIn("H₂O(l)", res["results"][0]["content"])

        red = lookup_reference_data("reduction potential silver", limit=1)
        self.assertEqual(red["count"], 1)
        self.assertEqual(red["results"][0]["appendix_id"], "app-L")
        self.assertIn("Ag⁺", red["results"][0]["content"])


if __name__ == "__main__":
    unittest.main()
