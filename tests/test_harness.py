"""Tests for the 8B Model & MCP Harness HTTP bridge and tool dispatcher."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from textbook_mcp.harness import create_server, get_tool_definitions, invoke_tool


class TestHarnessLogic(unittest.TestCase):
    def test_tool_definitions_match_mcp_server(self) -> None:
        tools = get_tool_definitions()
        names = {t["name"] for t in tools}
        self.assertEqual(
            names,
            {
                "get_toc",
                "browse_chapter",
                "search_textbook",
                "read_section",
                "search_exercises",
                "get_exercise_bundle",
                "lookup_reference_data",
            },
        )
        for t in tools:
            self.assertIn("inputSchema", t)
            schema = t["inputSchema"]
            self.assertEqual(schema["type"], "object")
            raw_json = json.dumps(schema)
            self.assertNotIn('"anyOf"', raw_json)
            self.assertNotIn('"title"', raw_json)
            for prop_name, prop_schema in schema.get("properties", {}).items():
                self.assertIn("description", prop_schema, f"{t['name']}.{prop_name} missing description")

        by_name = {t["name"]: t["inputSchema"]["properties"] for t in tools}
        self.assertIn("enum", by_name["lookup_reference_data"]["category"])
        self.assertIn("enum", by_name["search_exercises"]["answer_filter"])

    def test_invoke_tool_success(self) -> None:
        res = invoke_tool("get_exercise_bundle", {"exercise_ids": ["1.1", "1.2"]})
        self.assertTrue(res["ok"])
        self.assertEqual(res["tool"], "get_exercise_bundle")
        self.assertEqual(len(res["result"]["exercises"]), 2)
        self.assertNotIn("answer_key", res["result"]["exercises"][0])
        self.assertGreater(res["estimated_tokens"], 0)
        self.assertEqual(res["mcp_envelope"]["method"], "tools/call")

        res_ans = invoke_tool(
            "get_exercise_bundle",
            {"exercise_ids": ["1.1"], "include_answers": True},
        )
        self.assertTrue(res_ans["ok"])
        self.assertIn("answer_key", res_ans["result"]["exercises"][0])

    def test_invoke_tool_enum_validation_error(self) -> None:
        res = invoke_tool("lookup_reference_data", {"query": "NH3", "category": "chemistry constants"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "validation_error")

    def test_invoke_tool_validation_error(self) -> None:
        res = invoke_tool("browse_chapter", {"chapter": "not-an-int"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "validation_error")
        self.assertTrue(len(res["validation_errors"]) >= 1)

    def test_invoke_unknown_tool(self) -> None:
        res = invoke_tool("nonexistent_tool", {})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_type"], "unknown_tool")


class TestHarnessHTTP(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server("127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_serves_html_and_api(self) -> None:
        base = f"http://127.0.0.1:{self.port}"
        with urllib.request.urlopen(f"{base}/") as resp:
            html = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn("8B Model &amp; MCP Harness", html)

        with urllib.request.urlopen(f"{base}/api/tools") as resp:
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 200)
            self.assertEqual(data["server"], "chemistry-2e")
            self.assertEqual(len(data["tools"]), 7)

        req = urllib.request.Request(
            f"{base}/api/call",
            data=json.dumps({"name": "browse_chapter", "arguments": {"chapter": 5}}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["chapter"], 5)

        with urllib.request.urlopen(f"{base}/media/CNX_Chem_01_00_DailyChem.jpg") as resp:
            img_bytes = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "image/jpeg")
            self.assertGreater(len(img_bytes), 1000)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{base}/media/does_not_exist.jpg")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
