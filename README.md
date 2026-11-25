# General Chemistry 2e Local MCP Server

Deterministic local retrieval and exercise-verification MCP server (`stdio`) built from the **OpenStax Chemistry 2e** CNXML bundle (`osbooks-chemistry-bundle`) and backed by **SQLite + FTS5**.

## Setup & Ingestion

```powershell
# 1. Sync Python 3.12 environment
uv sync --python 3.12

# 2. Ingest OpenStax Chemistry 2e CNXML into data/chemistry2e.db
.\.venv\Scripts\textbook-ingest.exe
```

## Running the MCP Server (`stdio`)

```powershell
.\.venv\Scripts\textbook-mcp.exe
```

## Running the 8B Model & Harness Web App

```powershell
.\.venv\Scripts\python.exe -m textbook_mcp.harness
```

This opens a local browser UI (`http://127.0.0.1:8765`) with a tool pulldown, a dynamic form generated from each tool's JSON Schema, live simulated 8B tool-call payloads, an 8B context token meter, and formatted/raw MCP responses.

Example MCP client configuration:

```json
{
  "mcpServers": {
    "chemistry-2e": {
      "command": "C:\\Users\\ktheis\\Documents\\textbook_mcp\\.venv\\Scripts\\textbook-mcp.exe"
    }
  }
}
```

## Exposed Tools

| Tool | Purpose |
| --- | --- |
| `browse_chapter` | Chapter sections, learning objectives, and figure/table/example/exercise counts (`chapter=0` returns the book Table of Contents) |
| `search_textbook` | BM25 full-text search over exposition chunks with canonical section citations |
| `read_section` | Paginated section reader (~1500-token chunks) with figure references |
| `search_exercises` | Paginated search/browse over end-of-chapter exercises by keyword, chapter, section, and answer-key filter |
| `get_exercise_bundle` | Fetch exact exercises paired with official odd-numbered answer keys (`unavailable` for even; hidden by default) |
| `lookup_reference_data` | Query OpenStax Appendix reference tables (thermodynamics, equilibrium, constants, periodic table) |
