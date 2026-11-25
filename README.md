This project was written by prompting Gemini 4 (Hydrogen or Neon) in Antigravity in November 2026. I have not read the code.

# Textbook MCP Server (OpenStax Chemistry 2e)

A deterministic, local Model Context Protocol (`stdio`) server and SQLite + FTS5 knowledge base designed to ground small local LLMs (e.g., 8B models) in an open textbook—returning compact, citation-preserving Markdown responses to minimize prompt tokens and CPU prefill time.

The repository ships **code only** (no copyrighted or bulk textbook content). You supply the textbook source files in `data/raw/`, run the ingestion pipeline to generate a local SQLite database, and point your MCP client—or the included browser harness—at the server.

---

## Quick Start: OpenStax Chemistry 2e

### Prerequisites
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- `git`

### 1. Clone and install dependencies

```powershell
git clone https://github.com/ktheis/textbook_mcp.git
cd textbook_mcp
uv sync --python 3.12
```

### 2. Place the OpenStax Chemistry 2e CNXML bundle in `data/raw/`

OpenStax publishes its textbook source bundles (CNXML modules + figure images) publicly on GitHub under CC BY 4.0. Clone the Chemistry 2e bundle into `data/raw/osbooks-chemistry-bundle`:

```powershell
git clone --depth 1 https://github.com/openstax/osbooks-chemistry-bundle.git data/raw/osbooks-chemistry-bundle
```

The ingestion script expects this layout under `data/raw/`:

```text
data/
└── raw/
    └── osbooks-chemistry-bundle/
        ├── collections/
        │   └── chemistry-2e.collection.xml
        ├── modules/
        │   └── m68663/index.cnxml  (etc.)
        └── media/
            └── CNX_Chem_*.jpg      (figure images served by the harness UI)
```

*(Everything under `data/raw/` and `data/*.db*` is listed in `.gitignore`, so your local textbook files and generated database stay out of Git.)*

### 3. Ingest the textbook into SQLite + FTS5

```powershell
# Windows
.\.venv\Scripts\textbook-ingest.exe

# macOS / Linux
.venv/bin/textbook-ingest
```

Custom paths are supported via CLI flags:

```powershell
.\.venv\Scripts\textbook-ingest.exe --bundle data/raw/osbooks-chemistry-bundle --db data/chemistry2e.db
```

This parses the CNXML collection, chunks exposition sections (~2,200 chars/chunk), extracts figures/tables/worked examples, pairs odd-numbered exercises with official answer keys (marking even-numbered exercises as `unavailable`), indexes appendix reference tables, and builds SQLite FTS5 indexes in `data/chemistry2e.db`.

### 4. Verify with the test suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

---

## Running the Server & Local Harness

### MCP Server (`stdio`)

```powershell
.\.venv\Scripts\textbook-mcp.exe
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "chemistry-2e": {
      "command": "C:\\Users\\ktheis\\Documents\\textbook_mcp\\.venv\\Scripts\\textbook-mcp.exe",
      "env": {
        "TEXTBOOK_DB_PATH": "C:\\Users\\ktheis\\Documents\\textbook_mcp\\data\\chemistry2e.db"
      }
    }
  }
}
```

*(If `TEXTBOOK_DB_PATH` is omitted, the server defaults to `data/chemistry2e.db` relative to the repo root.)*

### 8B Model & MCP Harness Web App

```powershell
.\.venv\Scripts\python.exe -m textbook_mcp.harness
```

Opens a local browser UI (`http://127.0.0.1:8765`) with:
- A schema-driven tool form for every MCP tool
- Live simulated tool-call JSON payloads
- An 8B context-window token meter
- Rendered Markdown responses (including pipe tables and clickable figure image previews served from `data/raw/osbooks-chemistry-bundle/media/`) alongside raw text and JSON-RPC envelope views

---

## Exposed MCP Tools

All 6 tools return compact Markdown strings (`str`) with canonical citations (`Section 5.3`, `app-G`, `Exercise 1.1`):

| Tool | Purpose |
| --- | --- |
| `browse_chapter` | Chapter sections, boilerplate-stripped learning objectives, and counts of figures/tables/examples/exercises (`chapter=0` returns the compact book Table of Contents: all chapters + appendices) |
| `search_textbook` | BM25 full-text search over exposition chunks with `section_id` and `chunk_index` pointers for `read_section` |
| `read_section` | Paginated section/appendix reader (`chunk_index`, `max_chunks`) with figure captions, alt text, and media paths |
| `search_exercises` | Paginated search/browse (`offset`, `limit`) over end-of-chapter exercises by keyword, chapter, section, and answer-key filter (omits answer keys) |
| `get_exercise_bundle` | Fetch full problem statements for specific exercise IDs; official answer keys are hidden by default (`include_answers=False`) |
| `lookup_reference_data` | Query OpenStax Appendix reference tables (thermodynamic values, $K_a/K_b/K_{sp}$, reduction potentials, physical constants, periodic table) |

---

## Adapting This Repo to Another Textbook

The core SQLite schema (`src/textbook_mcp/db.py`), CNXML parser (`src/textbook_mcp/cnxml.py`), and harness UI (`src/textbook_mcp/harness.py`) are reusable, **but several parts of the code are tailored to OpenStax Chemistry 2e** (different textbooks have different chapter counts, exercise layouts, and appendix resources—for instance, most books do not have tables of standard enthalpies or $K_a$ values).

### Option A: Another OpenStax CNXML Textbook (e.g., *University Physics*, *Biology 2e*, *Calculus*)

1. **Download the OpenStax bundle** into `data/raw/<your-bundle>/` and check its `collections/*.collection.xml` filename.
2. **Update `src/textbook_mcp/ingest.py`:**
   - Change `DEFAULT_BUNDLE_DIR` and the collection filename in `_parse_collection()` (currently `collections/chemistry-2e.collection.xml`).
   - Adjust `_categorize_appendix(title)` to match your textbook's appendix topics (or return `"general"` if your book has no categorized data tables).
   - Check how end-of-chapter exercises and solutions are structured in your bundle's CNXML (some OpenStax books place answer keys in separate solution modules or inline `<solution>` tags).
3. **Update `src/textbook_mcp/db.py` (optional):**
   - Change `DEFAULT_DB_PATH` (e.g., `data/physics.db`), or pass `--db` during ingestion and set `TEXTBOOK_DB_PATH` at runtime.
4. **Update `src/textbook_mcp/server.py`:**
   - Update `MCPServer("chemistry-2e")`, the TOC header title in `browse_chapter(0)`, valid chapter range hints (`1-21`), and chemistry-specific examples in tool docstrings / `Field(description=...)`.
   - **Customize or remove `lookup_reference_data`:** `_PROP_CATEGORY_HINTS`, `_COMPOUND_ALIASES`, `_SUB_TO_ASCII`, and the `category` `Literal[...]` enum are Chemistry 2e–specific. Replace them with your book's appendix categories and terminology—or remove the tool entirely if your textbook has no reference tables.
5. **Update `src/textbook_mcp/harness.py`:**
   - Point `MEDIA_DIR` at `data/raw/<your-bundle>/media` so figure previews resolve.
6. **Update `tests/test_smoke.py` and `tests/test_harness.py`:**
   - Replace the Chemistry 2e–specific assertions (chapter/section counts, `"Hess law enthalpy"`, `"NH3"`, etc.) with checks for your textbook.

### Option B: Non-OpenStax Textbooks (Markdown, HTML, EPUB, LaTeX, PDF)

The SQLite schema in `src/textbook_mcp/db.py` is textbook-agnostic:

- `chapters`, `sections`, `chunks` (+ `chunks_fts`)
- `figures`, `book_tables`, `examples`
- `exercises` (+ `exercises_fts`)
- `reference_tables` (+ `reference_fts`)

To use a non-CNXML source, write a custom ingestion script that populates those tables and calls `rebuild_fts(conn)` at the end. Once the database is populated, `server.py` and `harness.py` work with only the domain-specific tweaks noted in steps 4–6 above.
