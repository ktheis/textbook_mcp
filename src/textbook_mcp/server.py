"""Local MCP stdio server for OpenStax Chemistry 2e."""

from __future__ import annotations

import difflib
import os
import re
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from textbook_mcp.db import DEFAULT_DB_PATH, get_connection

mcp = MCPServer("chemistry-2e")

# ~1500 tokens ≈ 5500-6000 characters
MAX_RESPONSE_CHARS = 5800


def _db_path() -> Path:
    env = os.environ.get("TEXTBOOK_DB_PATH")
    return Path(env) if env else DEFAULT_DB_PATH


def _conn() -> sqlite3.Connection:
    return get_connection(_db_path(), read_only=True)


_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "to", "for", "with", "by", "from", "at",
    "is", "are", "was", "were", "be", "been", "being", "and", "or", "but", "not",
    "this", "that", "these", "those", "it", "its", "as", "about", "into", "through",
    "how", "what", "why", "when", "where", "which", "who", "does", "do", "did",
    "can", "could", "would", "should", "will", "may", "might", "between", "among",
    "explain", "describe", "define", "discuss", "identify", "distinguish",
})


def _fts_query(raw: str) -> str:
    """Sanitize a user query into a safe FTS5 query (AND-joined tokens)."""
    tokens = re.findall(r"[A-Za-z0-9_]+", raw)
    if not tokens:
        return ""
    return " ".join(f'"{t}"' for t in tokens)


def _fts_queries(raw: str) -> tuple[str, str]:
    """Build (strict_and_query, broadened_or_query) with stopword stripping for natural-language prompts."""
    tokens = re.findall(r"[A-Za-z0-9_]+", raw)
    if not tokens:
        return "", ""
    filtered = [t for t in tokens if t.lower() not in _STOPWORDS] or tokens
    and_q = " ".join(f'"{t}"' for t in filtered)
    or_q = " OR ".join(f'"{t}"' for t in filtered)
    return and_q, or_q


def _cap_text(text: str, max_chars: int = MAX_RESPONSE_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n\n...[truncated; use pagination or narrower query]", True


def _closest_sections(conn: sqlite3.Connection, bad_id: str, limit: int = 5) -> list[dict[str, str]]:
    """Return closest valid section IDs for instructive error recovery."""
    rows = conn.execute("SELECT id, title FROM sections ORDER BY rowid").fetchall()
    by_id = {r["id"]: r["title"] for r in rows}
    all_ids = list(by_id.keys())

    # Normalize common 8B mistakes like "Section 5.3", "Ch 5", "app G", "5"
    cleaned = re.sub(r"^(?:section|sec\.?|chapter|ch\.?)\s*", "", bad_id.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"^appendix\s+", "app-", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^app\s+", "app-", cleaned, flags=re.IGNORECASE)

    candidates: list[str] = []
    if cleaned in by_id:
        candidates.append(cleaned)
    for sid in all_ids:
        if sid not in candidates and (
            sid.lower() == cleaned.lower()
            or (cleaned and sid.lower().startswith(cleaned.lower() + "."))
            or (cleaned and sid.lower().startswith(cleaned.lower()))
        ):
            candidates.append(sid)

    for match in difflib.get_close_matches(cleaned or bad_id, all_ids, n=limit, cutoff=0.3):
        if match not in candidates:
            candidates.append(match)

    return [{"section_id": sid, "title": by_id[sid]} for sid in candidates[:limit]]


@mcp.tool()
def get_toc() -> dict[str, Any]:
    """Return the full Table of Contents (all 21 chapters, sections, and appendices). Prefer search_textbook or browse_chapter first; call get_toc only when you do not know which chapter or appendix covers a topic."""
    with _conn() as conn:
        chapters = conn.execute(
            "SELECT number, title, section_count FROM chapters ORDER BY number"
        ).fetchall()
        sections = conn.execute(
            "SELECT id, chapter_num, title, kind FROM sections ORDER BY rowid"
        ).fetchall()

    chap_map: dict[int, dict[str, Any]] = {
        c["number"]: {
            "chapter": c["number"],
            "title": c["title"],
            "sections": [],
        }
        for c in chapters
    }
    appendices: list[dict[str, str]] = []

    for s in sections:
        if s["kind"] == "appendix":
            appendices.append({"id": s["id"], "title": s["title"]})
        elif s["chapter_num"] in chap_map and s["kind"] == "section":
            chap_map[s["chapter_num"]]["sections"].append({"id": s["id"], "title": s["title"]})

    return {
        "book": "OpenStax Chemistry 2e",
        "chapters": list(chap_map.values()),
        "appendices": appendices,
    }


@mcp.tool()
def browse_chapter(
    chapter: Annotated[int, Field(description="Chapter number from 1 to 21 (e.g. 5 for Thermochemistry).")],
) -> dict[str, Any]:
    """Inspect a chapter's sections, learning objectives, tables, examples, and figure/exercise counts. Use when you know the chapter number (1-21) and want to pick a section_id; call read_section for figure links and descriptions."""
    with _conn() as conn:
        chap = conn.execute(
            "SELECT number, title FROM chapters WHERE number = ?", (chapter,)
        ).fetchone()
        if chap is None:
            return {"error": f"Chapter {chapter} not found (valid range: 1-21)."}

        sections = conn.execute(
            "SELECT id, title, kind, learning_objectives FROM sections WHERE chapter_num = ? ORDER BY rowid",
            (chapter,),
        ).fetchall()
        fig_count = conn.execute(
            "SELECT COUNT(*) FROM figures WHERE chapter_num = ?",
            (chapter,),
        ).fetchone()[0]
        tables = conn.execute(
            "SELECT label, section_id, title FROM book_tables WHERE chapter_num = ? ORDER BY rowid",
            (chapter,),
        ).fetchall()
        examples = conn.execute(
            "SELECT label, section_id, title FROM examples WHERE chapter_num = ? ORDER BY rowid",
            (chapter,),
        ).fetchall()
        ex_counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(has_answer), 0) AS keyed FROM exercises WHERE chapter_num = ?",
            (chapter,),
        ).fetchone()

    return {
        "chapter": chap["number"],
        "title": chap["title"],
        "sections": [
            {
                "id": s["id"],
                "title": s["title"],
                "kind": s["kind"],
                "learning_objectives": s["learning_objectives"][:600],
            }
            for s in sections
        ],
        "figures_summary": {
            "total": fig_count,
            "hint": "Call read_section(section_id=...) for figure links and descriptions.",
        },
        "tables": [
            {"citation": t["label"], "section_id": t["section_id"], "title": t["title"]}
            for t in tables
        ],
        "examples": [
            {"citation": e["label"], "section_id": e["section_id"], "title": e["title"]}
            for e in examples
        ],
        "exercises_summary": {
            "total": ex_counts["total"],
            "with_official_answer_key": ex_counts["keyed"],
            "without_answer_key": ex_counts["total"] - ex_counts["keyed"],
        },
    }


@mcp.tool()
def search_textbook(
    query: Annotated[str, Field(description="2-5 chemistry keywords (not a full sentence), e.g. 'Hess law enthalpy' or 'hydrogen bonding density'.")],
    chapter: Annotated[int, Field(description="Optional chapter filter (1-21). Omit or pass 0 to search all chapters.")] = 0,
    limit: Annotated[int, Field(description="Maximum results to return (1-12, default 5).")] = 5,
) -> dict[str, Any]:
    """Use for conceptual questions, definitions, formulas, and worked examples in Chemistry 2e. Pass 2-5 chemistry keywords (not full sentences). Returns snippets with section_id and chunk_index to pass to read_section."""
    and_q, or_q = _fts_queries(query)
    if not and_q:
        return {"query": query, "results": [], "note": "Empty or non-alphanumeric query."}

    limit = max(1, min(limit, 12))
    chapter_filter = chapter if chapter and chapter > 0 else None
    base_sql = """
        SELECT
            c.section_id,
            c.chunk_index,
            c.heading,
            c.content,
            snippet(chunks_fts, 2, '', '', '...', 64) AS fts_snippet,
            s.title AS section_title,
            s.chapter_num
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN sections s ON s.id = c.section_id
        WHERE chunks_fts MATCH ?
    """

    def _run_search(fts_q: str) -> list[sqlite3.Row]:
        sql = base_sql
        params: list[Any] = [fts_q]
        if chapter_filter is not None:
            sql += " AND s.chapter_num = ?"
            params.append(chapter_filter)
        sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
        params.append(limit)
        with _conn() as conn:
            return conn.execute(sql, params).fetchall()

    match_mode = "and"
    rows = _run_search(and_q)
    if not rows and or_q != and_q:
        rows = _run_search(or_q)
        match_mode = "or_fallback"

    results = []
    for r in rows:
        full_content = r["content"]
        if len(full_content) <= 750:
            snippet = full_content
            truncated = False
        else:
            raw_snippet = (r["fts_snippet"] or full_content).strip()
            if len(raw_snippet) > 750:
                raw_snippet = raw_snippet[:750].rstrip() + "..."
            truncated = len(raw_snippet) < len(full_content) or raw_snippet != full_content
            snippet = (
                f"{raw_snippet}\n\n"
                f"[excerpt; call read_section(section_id='{r['section_id']}', chunk_index={r['chunk_index']}) for full text]"
                if truncated
                else raw_snippet
            )
        results.append({
            "citation": f"Section {r['section_id']}" if not r["section_id"].startswith("app-") else r["section_id"],
            "section_id": r["section_id"],
            "section_title": r["section_title"],
            "heading": r["heading"],
            "chunk_index": r["chunk_index"],
            "truncated": truncated,
            "snippet": snippet,
        })

    return {
        "query": query,
        "chapter_filter": chapter_filter,
        "match_mode": match_mode,
        "count": len(results),
        "results": results,
    }


@mcp.tool()
def read_section(
    section_id: Annotated[str, Field(description="Canonical section or appendix ID, e.g. '5.3', '6.5', or 'app-G'.")],
    chunk_index: Annotated[int, Field(description="0-based starting chunk index for pagination (default 0). Use next_chunk_index from the previous call to continue.")] = 0,
    max_chunks: Annotated[int, Field(description="Number of chunks to return per call (1-4, default 2).")] = 2,
) -> dict[str, Any]:
    """Read full textbook exposition for a specific section_id (including worked examples and tables) with pagination. Use after search_textbook or browse_chapter to read complete explanations."""
    chunk_index = max(0, chunk_index)
    max_chunks = max(1, min(max_chunks, 4))

    with _conn() as conn:
        sec = conn.execute(
            "SELECT id, chapter_num, title, kind, learning_objectives FROM sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if sec is None:
            closest = _closest_sections(conn, section_id)
            return {
                "error": f"Section '{section_id}' not found. Expected a canonical ID like '5.3' or 'app-G'.",
                "closest_matches": closest,
            }

        chunks = conn.execute(
            "SELECT chunk_index, heading, content FROM chunks WHERE section_id = ? ORDER BY chunk_index",
            (section_id,),
        ).fetchall()
        figs = conn.execute(
            "SELECT label, caption, alt_text, media_path FROM figures WHERE section_id = ? ORDER BY rowid",
            (section_id,),
        ).fetchall()

    total_chunks = len(chunks)
    selected = chunks[chunk_index : chunk_index + max_chunks]

    body_parts = []
    total_len = 0
    included = 0
    for ch in selected:
        block = f"### {ch['heading']}\n{ch['content']}" if ch["heading"] else ch["content"]
        if included > 0 and total_len + len(block) > MAX_RESPONSE_CHARS:
            break
        capped, _ = _cap_text(block, MAX_RESPONSE_CHARS)
        body_parts.append(capped)
        total_len += len(capped)
        included += 1

    next_idx = chunk_index + included
    has_more = next_idx < total_chunks

    return {
        "citation": f"Section {sec['id']}" if sec["kind"] in {"section", "intro"} else sec["id"],
        "section_id": sec["id"],
        "title": sec["title"],
        "learning_objectives": sec["learning_objectives"] if chunk_index == 0 else "",
        "chunk_range": f"{chunk_index}-{max(chunk_index, next_idx - 1)}" if total_chunks else "empty",
        "total_chunks": total_chunks,
        "has_more": has_more,
        "next_chunk_index": next_idx if has_more else None,
        "content": "\n\n".join(body_parts),
        "figures_in_section": [
            {
                "citation": f["label"],
                "caption": f["caption"][:160],
                "alt_text": f["alt_text"][:240],
                "media_path": f["media_path"],
            }
            for f in figs
        ] if chunk_index == 0 else [],
    }


@mcp.tool()
def search_exercises(
    query: Annotated[str, Field(description="2-5 chemistry keywords to match in problem text (e.g. 'calorimetry enthalpy'). Leave empty to browse by chapter or section.")] = "",
    chapter: Annotated[int, Field(description="Optional chapter filter (1-21). Omit or pass 0 for all chapters.")] = 0,
    section_id: Annotated[str, Field(description="Optional section ID filter, e.g. '5.3'. Leave empty for all sections.")] = "",
    answer_filter: Annotated[
        Literal["any", "with_answer", "without_answer"],
        Field(description="Filter by official answer-key availability: 'any' (default), 'with_answer' (odd-numbered keyed exercises), or 'without_answer' (unkeyed exercises)."),
    ] = "any",
    limit: Annotated[int, Field(description="Maximum exercises to return (1-15, default 8).")] = 8,
) -> dict[str, Any]:
    """Search end-of-chapter practice problems by keywords, chapter, section_id, or answer-key availability. Returns problem statements only (no answer keys) so you can assign problems without spoiling answers."""
    limit = max(1, min(limit, 15))
    query_clean = query.strip()
    section_clean = section_id.strip()
    chapter_filter = chapter if chapter and chapter > 0 else None
    and_q, or_q = _fts_queries(query_clean) if query_clean else ("", "")

    def _run_ex_query(fts_q: str) -> list[sqlite3.Row]:
        params: list[Any] = []
        if fts_q:
            sql = """
                SELECT e.id, e.chapter_num, e.exercise_num, e.section_id, e.problem_text, e.has_answer
                FROM exercises_fts f
                JOIN exercises e ON e.rowid = f.rowid
                WHERE exercises_fts MATCH ?
            """
            params.append(fts_q)
            order = "ORDER BY bm25(exercises_fts)"
        else:
            sql = "SELECT e.id, e.chapter_num, e.exercise_num, e.section_id, e.problem_text, e.has_answer FROM exercises e WHERE 1=1"
            order = "ORDER BY e.chapter_num, e.exercise_num"

        if chapter_filter is not None:
            sql += " AND e.chapter_num = ?"
            params.append(chapter_filter)
        if section_clean:
            sql += " AND e.section_id = ?"
            params.append(section_clean)
        if answer_filter == "with_answer":
            sql += " AND e.has_answer = 1"
        elif answer_filter == "without_answer":
            sql += " AND e.has_answer = 0"

        sql += f" {order} LIMIT ?"
        params.append(limit)
        with _conn() as conn:
            return conn.execute(sql, params).fetchall()

    match_mode = "filter" if not and_q else "and"
    rows = _run_ex_query(and_q)
    if and_q and not rows and or_q != and_q:
        rows = _run_ex_query(or_q)
        match_mode = "or_fallback"

    items = []
    for r in rows:
        prob, _ = _cap_text(r["problem_text"], 600)
        items.append({
            "id": r["id"],
            "section_id": r["section_id"],
            "has_answer": bool(r["has_answer"]),
            "answer_status": "verified_official_key" if r["has_answer"] else "unavailable",
            "problem": prob,
        })

    return {
        "match_mode": match_mode,
        "answer_filter": answer_filter,
        "count": len(items),
        "exercises": items,
        "note": "Answer keys are omitted from search results. Call get_exercise_bundle(exercise_ids=[...], include_answers=true) only after the student attempts a problem.",
    }


@mcp.tool()
def get_exercise_bundle(
    exercise_ids: Annotated[list[str], Field(description="List of exercise IDs, e.g. ['Exercise 1.1', 'Exercise 1.2'] or ['1.1', '1.2'] (max 15).")],
    include_answers: Annotated[bool, Field(description="Include official answer keys when available (default false). Set true only after the student attempts the problem and you need to verify or explain it.")] = False,
) -> dict[str, Any]:
    """Retrieve full problem statements for specific exercise IDs. Official answer keys are hidden by default to avoid spoiling answers during tutoring; set include_answers=true only after the student attempts the problem."""
    if not exercise_ids:
        return {"include_answers": include_answers, "exercises": [], "not_found": []}

    # Normalize inputs like "1.1" -> "Exercise 1.1"
    normalized = [
        eid if eid.lower().startswith("exercise ") else f"Exercise {eid.strip()}"
        for eid in exercise_ids[:15]
    ]
    placeholders = ",".join("?" for _ in normalized)

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, chapter_num, exercise_num, section_id, problem_text, answer_text, has_answer FROM exercises WHERE id IN ({placeholders})",
            normalized,
        ).fetchall()

    by_id = {r["id"]: r for r in rows}
    found = []
    not_found = []
    for eid in normalized:
        r = by_id.get(eid)
        if r is None:
            not_found.append(eid)
            continue
        item: dict[str, Any] = {
            "id": r["id"],
            "section_id": r["section_id"],
            "has_answer": bool(r["has_answer"]),
            "answer_status": "verified_official_key" if r["has_answer"] else "unavailable",
            "problem": r["problem_text"],
        }
        if include_answers:
            item["answer_key"] = r["answer_text"] if r["has_answer"] else "unavailable"
        found.append(item)

    return {"include_answers": include_answers, "exercises": found, "not_found": not_found}


_SUB_TO_ASCII = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾", "0123456789+-=()0123456789+-=()")

# Property keywords (harvested from PQcalc naming_conventions) -> (category, optional appendix_id)
_PROP_CATEGORY_HINTS: list[tuple[tuple[str, ...], str, str | None]] = [
    (
        (
            "entropy", "molar entropy", "enthalpy", "molar enthalpy",
            "heat of formation", "standard heat of formation",
            "gibbs", "free energy", "molar gibbs energy",
            "dhf", "dgf", "hf", "gf", "thermodynamic", "thermodynamics",
        ),
        "thermodynamics",
        "app-G",
    ),
    (("acid dissociation", "weak acid", "ka", "pka"), "equilibrium", "app-H"),
    (("base dissociation", "weak base", "kb", "pkb"), "equilibrium", "app-I"),
    (("solubility product", "ksp"), "equilibrium", "app-J"),
    (("formation constant", "complex ion", "kf"), "equilibrium", "app-K"),
    (
        (
            "reduction potential", "oxidation potential", "cell potential",
            "electrode potential", "half cell", "half-cell", "ered", "eox", "ecell",
        ),
        "equilibrium",
        "app-L",
    ),
    (("ionization constant", "equilibrium"), "equilibrium", None),
    (
        (
            "specific heat", "heat capacity", "vapor pressure",
            "boiling point elevation", "freezing point depression",
            "cryoscopic", "ebullioscopic", "kw", "pkw",
            "avogadro", "planck", "boltzmann", "gas constant",
            "speed of light", "gravitational", "conversion", "physical constant",
        ),
        "constants",
        None,
    ),
    (("half life", "half-life", "halflife", "isotope", "radioactive"), "general", "app-M"),
    (("commercial acid", "commercial base"), "general", "app-F"),
]

# High-frequency Chemistry 2e compound names (harvested from PQcalc chemdatabase) -> ASCII formula aliases
_COMPOUND_ALIASES: list[tuple[str, str]] = [
    ("calcium hydroxide", "ca(oh)2"),
    ("calcium chloride", "cacl2"),
    ("calcium oxide", "cao"),
    ("carbon dioxide", "co2"),
    ("carbon monoxide", "co"),
    ("dihydrogen phosphate", "h2po4-"),
    ("hydrogen sulfate", "hso4-"),
    ("dihydrogen sulfide", "h2s"),
    ("dihidrogen sulfide", "h2s"),
    ("hydrogen sulfide", "h2s"),
    ("hydrogen chloride", "hcl"),
    ("hydrochloric acid", "hcl"),
    ("iron(iii) oxide", "fe2o3"),
    ("iron(iii)oxide", "fe2o3"),
    ("nitrogen dioxide", "no2"),
    ("nitrogen oxide", "no"),
    ("nitrogenoxide", "no"),
    ("nitric oxide", "no"),
    ("phosphoric acid", "h3po4"),
    ("sodium hydroxide", "naoh"),
    ("sodium chloride", "nacl"),
    ("sulfur dioxide", "so2"),
    ("sulfur trioxide", "so3"),
    ("white phosphorus", "p4"),
    ("white phosphorous", "p4"),
    ("water vapor", "h2o(g)"),
    ("liquid water", "h2o(l)"),
    ("hydrogen gas", "h2(g)"),
    ("nitrogen gas", "n2(g)"),
    ("oxygen gas", "o2(g)"),
    ("chlorine gas", "cl2(g)"),
    ("ammonia gas", "nh3(g)"),
    ("methane gas", "ch4(g)"),
    ("liquid mercury", "hg(l)"),
    ("hydronium", "h3o+"),
    ("hydroxide", "oh-"),
    ("hydroxid", "oh-"),
    ("chloride", "cl-"),
    ("nitrate", "no3-"),
    ("phosphate", "po4"),
    ("ammonia", "nh3"),
    ("methane", "ch4"),
    ("ethanol", "c2h5oh"),
    ("dioxygen", "o2"),
    ("dinitrogen", "n2"),
    ("water", "h2o"),
    ("potassium", "k"),
    ("sodium", "na"),
    ("magnesium", "mg"),
    ("calcium", "ca"),
    ("strontium", "sr"),
    ("aluminum", "al"),
    ("aluminium", "al"),
    ("phosphorus", "p"),
    ("phosphorous", "p"),
    ("chlorine", "cl"),
    ("silver", "ag"),
    ("copper", "cu"),
    ("iron", "fe"),
    ("zinc", "zn"),
    ("mercury", "hg"),
    ("sulfur", "s"),
]


def _normalize_chem(text: str) -> str:
    return text.translate(_SUB_TO_ASCII).lower()


def _parse_reference_query(query: str, explicit_cat: str) -> tuple[str, str | None, list[str], list[str]]:
    """Infer category/appendix from property keywords and extract substance terms + formula aliases."""
    norm = _normalize_chem(query)
    inferred_cat = explicit_cat
    inferred_app: str | None = None
    remaining = norm

    for phrases, cat, app_id in _PROP_CATEGORY_HINTS:
        for phrase in sorted(phrases, key=len, reverse=True):
            pattern = rf"\b{re.escape(phrase)}\b"
            if re.search(pattern, remaining):
                if not inferred_cat:
                    inferred_cat = cat
                if inferred_app is None and app_id is not None:
                    inferred_app = app_id
                remaining = re.sub(pattern, " ", remaining)

    aliases: list[str] = []
    for name, formula in _COMPOUND_ALIASES:
        pattern = rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])"
        if re.search(pattern, remaining):
            aliases.append(formula)
            remaining = re.sub(pattern, " ", remaining)

    raw_terms = [
        t for t in re.findall(r"[a-z0-9()+_-]+", remaining)
        if t not in _STOPWORDS
    ]
    substance_terms = list(dict.fromkeys(aliases + raw_terms))
    all_terms = list(dict.fromkeys(
        aliases + [t for t in re.findall(r"[a-z0-9]+", norm) if t not in _STOPWORDS]
    ))
    return inferred_cat, inferred_app, substance_terms, all_terms


@mcp.tool()
def lookup_reference_data(
    query: Annotated[str, Field(description="Chemical formula, substance name, or constant name (e.g. 'NH3', 'entropy water', 'acetic acid', 'gas constant').")],
    category: Annotated[
        Literal["all", "thermodynamics", "equilibrium", "constants", "periodic_table", "general"],
        Field(description="Appendix category filter (default 'all')."),
    ] = "all",
    limit: Annotated[int, Field(description="Maximum matching reference tables to return (1-5, default 3).")] = 3,
) -> dict[str, Any]:
    """Look up exact numeric data from OpenStax Chemistry 2e Appendices (standard enthalpies/entropies/free energies, Ka/Kb/Ksp, physical constants, periodic table). Use instead of guessing numerical values."""
    limit = max(1, min(limit, 5))
    cat_filter = "" if category == "all" else category
    inferred_cat, inferred_app, substance_terms, all_terms = _parse_reference_query(query, cat_filter)
    fts_q = _fts_query(query)
    params: list[Any] = []

    if fts_q:
        sql = """
            SELECT r.id, r.appendix_id, r.title, r.category, r.content
            FROM reference_fts f
            JOIN reference_tables r ON r.rowid = f.rowid
            WHERE reference_fts MATCH ?
        """
        params.append(fts_q)
        order = "ORDER BY bm25(reference_fts)"
    else:
        sql = "SELECT r.id, r.appendix_id, r.title, r.category, r.content FROM reference_tables r WHERE 1=1"
        order = "ORDER BY r.id"

    if cat_filter:
        sql += " AND r.category = ?"
        params.append(cat_filter)

    sql += f" {order} LIMIT ?"
    params.append(limit)

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        # Fallback for chemical formulas (NH3 -> NH₃), property keywords (entropy -> thermodynamics),
        # and common compound names (water -> H₂O)
        if not rows and query.strip():
            fb_sql = "SELECT id, appendix_id, title, category, content FROM reference_tables WHERE 1=1"
            fb_params: list[Any] = []
            target_cat = cat_filter or inferred_cat
            if target_cat:
                fb_sql += " AND category = ?"
                fb_params.append(target_cat)
            candidate_rows = conn.execute(fb_sql, fb_params).fetchall()

            match_terms = substance_terms if substance_terms else all_terms
            scored: list[tuple[tuple[int, int], sqlite3.Row]] = []
            for r in candidate_rows:
                haystack = _normalize_chem(r["title"] + "\n" + r["content"])
                hits = sum(1 for t in match_terms if t in haystack)
                app_bonus = 1 if (inferred_app and r["appendix_id"] == inferred_app) else 0
                if hits > 0 or (target_cat and not match_terms):
                    scored.append(((app_bonus, hits), r))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            rows = [r for _, r in scored[:limit]]

    # Filter long tables down to matching lines + header so 8B context stays clean
    row_terms = substance_terms if substance_terms else all_terms
    results = []
    for r in rows:
        content = r["content"]
        if len(content) > 2500 and row_terms:
            lines = content.splitlines()
            sep_idx = next((i for i, ln in enumerate(lines[:6]) if "| ---" in ln), 2)
            header = lines[: sep_idx + 1]
            data_lines = lines[sep_idx + 1 :]
            exact_lines = [
                ln for ln in data_lines
                if all(t in _normalize_chem(ln) for t in row_terms)
            ]
            any_lines = [
                ln for ln in data_lines
                if any(t in _normalize_chem(ln) for t in row_terms)
            ]
            chosen = exact_lines if exact_lines else any_lines
            if chosen:
                content = "\n".join(header + chosen[:40])
        capped, truncated = _cap_text(content, 2500)
        results.append({
            "id": r["id"],
            "appendix_id": r["appendix_id"],
            "title": r["title"],
            "category": r["category"],
            "truncated": truncated,
            "content": capped,
        })

    return {
        "query": query,
        "category": category if category != "all" else (inferred_cat or "all"),
        "count": len(results),
        "results": results,
    }


def _strip_titles(node: Any) -> None:
    """Recursively remove auto-generated Pydantic 'title' keys to keep 8B tool schemas compact."""
    if isinstance(node, dict):
        node.pop("title", None)
        for value in node.values():
            _strip_titles(value)
    elif isinstance(node, list):
        for item in node:
            _strip_titles(item)


for _tool in mcp._tool_manager.list_tools():
    _strip_titles(_tool.parameters)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
