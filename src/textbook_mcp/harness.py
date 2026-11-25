"""Lightweight local HTTP harness modeling an 8B LLM + MCP tool-calling loop."""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import time
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from textbook_mcp.server import mcp

STATIC_DIR = Path(__file__).resolve().parent / "static"
HTML_PATH = STATIC_DIR / "harness.html"
MEDIA_DIR = (Path(__file__).resolve().parents[2] / "data" / "raw" / "osbooks-chemistry-bundle" / "media").resolve()


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return MCP tool definitions (name, description, JSON inputSchema)."""
    tools = mcp._tool_manager.list_tools()
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.parameters,
        }
        for tool in tools
    ]


def invoke_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate arguments against the tool's Pydantic/JSON schema and execute it."""
    args = arguments if arguments is not None else {}
    if not isinstance(args, dict):
        return {
            "ok": False,
            "error_type": "validation_error",
            "error": "Tool arguments must be a JSON object.",
            "latency_ms": 0.0,
        }

    tool = mcp._tool_manager.get_tool(name)
    if tool is None:
        return {
            "ok": False,
            "error_type": "unknown_tool",
            "error": f"Unknown tool: '{name}'",
            "latency_ms": 0.0,
        }

    t0 = time.perf_counter()
    try:
        validated = tool.fn_metadata.arg_model.model_validate(args)
        kwargs = validated.model_dump()
    except ValidationError as exc:
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "ok": False,
            "tool": name,
            "arguments": args,
            "error_type": "validation_error",
            "error": "Arguments failed JSON Schema validation.",
            "validation_errors": exc.errors(include_url=False),
            "latency_ms": elapsed_ms,
        }

    try:
        result = tool.fn(**kwargs)
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        serialized = json.dumps(result, ensure_ascii=False)
        char_count = len(serialized)
        est_tokens = max(1, math.ceil(char_count / 4))
        return {
            "ok": True,
            "tool": name,
            "arguments": kwargs,
            "latency_ms": elapsed_ms,
            "char_count": char_count,
            "estimated_tokens": est_tokens,
            "result": result,
            "mcp_envelope": {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": kwargs},
                "response": {
                    "content": [{"type": "text", "text": serialized}],
                    "isError": False,
                },
            },
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            "ok": False,
            "tool": name,
            "arguments": kwargs,
            "error_type": "execution_error",
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": elapsed_ms,
        }


class HarnessHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving the HTML app and MCP tool bridge API."""

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_bytes: bytes, status: int = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/index.html"}:
            if not HTML_PATH.exists():
                self._send_json({"error": f"Missing UI file: {HTML_PATH}"}, HTTPStatus.NOT_FOUND)
                return
            self._send_html(HTML_PATH.read_bytes())
            return

        if path == "/api/tools":
            self._send_json({
                "server": mcp.name,
                "tools": get_tool_definitions(),
            })
            return

        if path == "/api/health":
            self._send_json({"status": "ok", "server": mcp.name})
            return

        if path.startswith("/media/"):
            rel_name = urllib.parse.unquote(path[len("/media/"):]).lstrip("/")
            if not rel_name or "/" in rel_name or "\\" in rel_name:
                self._send_json({"error": "Invalid media path"}, HTTPStatus.BAD_REQUEST)
                return
            candidate = (MEDIA_DIR / rel_name).resolve()
            if not candidate.is_relative_to(MEDIA_DIR) or not candidate.is_file():
                self._send_json({"error": f"Media file not found: {rel_name}"}, HTTPStatus.NOT_FOUND)
                return
            content_type, _ = mimetypes.guess_type(candidate.name)
            data = candidate.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/call":
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"ok": False, "error": "Invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
            return

        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._send_json(
                {"ok": False, "error_type": "invalid_json", "error": f"Invalid JSON: {exc}"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            self._send_json(
                {"ok": False, "error_type": "invalid_request", "error": "Expected JSON object with string 'name'."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        response = invoke_tool(payload["name"], payload.get("arguments", {}))
        status = HTTPStatus.OK if response.get("ok") else HTTPStatus.BAD_REQUEST
        self._send_json(response, status)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Keep console output clean; override to silence per-request access logs unless desired.
        return


def create_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    """Create a configured ThreadingHTTPServer for the harness app."""
    return ThreadingHTTPServer((host, port), HarnessHandler)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the 8B Model & MCP Harness web app for OpenStax Chemistry 2e."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the app in the default browser.",
    )
    args = parser.parse_args()

    server = create_server(args.host, args.port)
    url = f"http://{args.host}:{args.port}"
    print(f"8B Model & MCP Harness running at {url}  (Press Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down harness server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
