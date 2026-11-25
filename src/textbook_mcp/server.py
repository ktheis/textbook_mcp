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


_LO_BOILERPLATE_RE = re.compile(
    r"^\s*By the end of this section, you will be able to:\s*(?:[-•]\s*)?",
    flags=re.IGNORECASE,
)


def _clean_objectives(raw: str, max_chars: int = 600) -> str:
    """Strip repeated OpenStax learning-objectives boilerplate and cap length."""
    cleaned = _LO_BOILERPLATE_RE.sub("", (raw or "").strip()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


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
def browse_chapter(
    chapter: Annotated[int, Field(description="Chapter number (1-21, e.g. 5 for Thermochemistry), or 0 for the book Table of Contents (all chapters and appendices).")] = 0,
) -> str:
    """List a chapter's sections, objectives, and counts of figures, tables, examples, and exercises. Pass chapter=0 for the full book Table of Contents (21 chapters + appendices)."""
    with _conn() as conn:
        if chapter == 0:
            chapters = conn.execute(
                "SELECT number, title, section_count FROM chapters ORDER BY number"
            ).fetchall()
            appendices = conn.execute(
                "SELECT id, title FROM sections WHERE kind = 'appendix' ORDER BY rowid"
            ).fetchall()

            lines = [
                "# OpenStax Chemistry 2e — Table of Contents",
                "Call browse_chapter(chapter=N) for a chapter's sections, or read_section(section_id='app-X') for an appendix.",
                "",
                "## Chapters",
            ]
            for c in chapters:
                lines.append(f"- **Ch {c['number']}**: {c['title']} ({c['section_count']} sections)")
            if appendices:
                lines.append("")
                lines.append("## Appendices")
                for a in appendices:
                    lines.append(f"- **{a['id']}**: {a['title']}")
            return "\n".join(lines)

        chap = conn.execute(
            "SELECT number, title FROM chapters WHERE number = ?", (chapter,)
        ).fetchone()
        if chap is None:
            return f"Error: Chapter {chapter} not found (valid range: 0 for TOC, or 1-21)."

        sections = conn.execute(
            "SELECT id, title, kind, learning_objectives FROM sections WHERE chapter_num = ? ORDER BY rowid",
            (chapter,),
        ).fetchall()
        fig_count = conn.execute(
            "SELECT COUNT(*) FROM figures WHERE chapter_num = ?",
            (chapter,),
        ).fetchone()[0]
        table_count = conn.execute(
            "SELECT COUNT(*) FROM book_tables WHERE chapter_num = ?",
            (chapter,),
        ).fetchone()[0]
        example_count = conn.execute(
            "SELECT COUNT(*) FROM examples WHERE chapter_num = ?",
            (chapter,),
        ).fetchone()[0]
        ex_counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(has_answer), 0) AS keyed FROM exercises WHERE chapter_num = ?",
            (chapter,),
        ).fetchone()
        sec_ex_rows = conn.execute(
            "SELECT section_id, COUNT(*) AS cnt FROM exercises WHERE chapter_num = ? GROUP BY section_id",
            (chapter,),
        ).fetchall()

    sec_ex_map = {r["section_id"]: r["cnt"] for r in sec_ex_rows}
    total_ex = ex_counts["total"]
    keyed_ex = ex_counts["keyed"]
    unkeyed_ex = total_ex - keyed_ex

    lines = [
        f"# Chapter {chap['number']}: {chap['title']}",
        "",
        (
            f"Figures: {fig_count} (call read_section for links/descriptions)"
            f" | Tables: {table_count}"
            f" | Examples: {example_count}"
            f" | Exercises: {total_ex} ({keyed_ex} with official key, {unkeyed_ex} unkeyed)"
        ),
        "",
        "## Sections",
    ]
    for s in sections:
        kind_tag = f" [{s['kind']}]" if s["kind"] != "section" else ""
        ex_n = sec_ex_map.get(s["id"], 0)
        ex_tag = f" ({ex_n} exercises)" if ex_n else ""
        lines.append(f"- **{s['id']}: {s['title']}**{kind_tag}{ex_tag}")
        obj = _clean_objectives(s["learning_objectives"], max_chars=600)
        if obj:
            lines.append(f"  Objectives: {obj}")

    return "\n".join(lines)


@mcp.tool()
def search_textbook(
    query: Annotated[str, Field(description="2-5 chemistry keywords (not a full sentence), e.g. 'Hess law enthalpy'.")],
    chapter: Annotated[int, Field(description="Chapter filter (1-21); 0 searches all.")] = 0,
    limit: Annotated[int, Field(description="Max results (1-12, default 5).")] = 5,
) -> str:
    """Search Chemistry 2e for concepts, definitions, formulas, and worked examples using 2-5 keywords. Returns snippets with section_id and chunk_index for read_section."""
    and_q, or_q = _fts_queries(query)
    if not and_q:
        return f'No results for "{query}": empty or non-alphanumeric query.'

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

    chap_note = f", chapter={chapter_filter}" if chapter_filter is not None else ""
    parts = [f'Search "{query}" ({len(rows)} results, mode={match_mode}{chap_note})']

    if not rows:
        parts.append("No matching textbook passages found.")
        return "\n\n".join(parts)

    for r in rows:
        full_content = r["content"]
        if len(full_content) <= 750:
            snippet = full_content
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
        citation = f"Section {r['section_id']}" if not r["section_id"].startswith("app-") else r["section_id"]
        header = f"## {citation} — {r['section_title']} (section_id={r['section_id']}, chunk={r['chunk_index']})"
        heading_line = f"*{r['heading']}*\n" if r["heading"] and r["heading"] != r["section_title"] else ""
        parts.append(f"{header}\n{heading_line}{snippet}")

    return "\n\n".join(parts)


@mcp.tool()
def read_section(
    section_id: Annotated[str, Field(description="Canonical section/appendix ID, e.g. '5.3', '6.5', or 'app-G'.")],
    chunk_index: Annotated[int, Field(description="0-based starting chunk (default 0). Use next_chunk_index to continue.")] = 0,
    max_chunks: Annotated[int, Field(description="Chunks to return (1-4, default 2).")] = 2,
) -> str:
    """Read full exposition, worked examples, and tables for a section_id with pagination. Use after search_textbook or browse_chapter."""
    chunk_index = max(0, chunk_index)
    max_chunks = max(1, min(max_chunks, 4))

    with _conn() as conn:
        sec = conn.execute(
            "SELECT id, chapter_num, title, kind, learning_objectives FROM sections WHERE id = ?",
            (section_id,),
        ).fetchone()
        if sec is None:
            closest = _closest_sections(conn, section_id)
            lines = [
                f"Error: Section '{section_id}' not found. Expected a canonical ID like '5.3' or 'app-G'.",
            ]
            if closest:
                lines.append("Closest valid section_id matches:")
                for m in closest:
                    lines.append(f"- `{m['section_id']}`: {m['title']}")
            return "\n".join(lines)

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
    citation = f"Section {sec['id']}" if sec["kind"] in {"section", "intro"} else sec["id"]

    if total_chunks:
        chunk_range = f"{chunk_index}-{max(chunk_index, next_idx - 1)}"
        next_hint = (
            f" | Next: read_section(section_id='{sec['id']}', chunk_index={next_idx})"
            if has_more
            else " (end of section)"
        )
        meta_line = f"Chunks {chunk_range} of {total_chunks}{next_hint}"
    else:
        meta_line = "Chunks empty (0 total)"

    parts = [f"# {citation}: {sec['title']}", meta_line]

    if chunk_index == 0 and sec["learning_objectives"]:
        obj = _clean_objectives(sec["learning_objectives"], max_chars=800)
        if obj:
            parts.append(f"**Objectives:** {obj}")

    if body_parts:
        parts.append("\n\n".join(body_parts))

    if chunk_index == 0 and figs:
        fig_lines = ["## Figures"]
        for f in figs:
            label = f["label"] or "Figure"
            media = f" (`{f['media_path']}`)" if f["media_path"] else ""
            cap = (f["caption"] or "").strip()[:160]
            alt = (f["alt_text"] or "").strip()[:240]
            desc_parts = []
            if cap:
                desc_parts.append(cap)
            if alt and alt != cap:
                desc_parts.append(f"Alt: {alt}")
            desc = " | ".join(desc_parts)
            fig_lines.append(f"- **{label}**{media}: {desc}" if desc else f"- **{label}**{media}")
        parts.append("\n".join(fig_lines))

    return "\n\n".join(parts)


@mcp.tool()
def search_exercises(
    query: Annotated[str, Field(description="2-5 keywords in problem text (e.g. 'calorimetry enthalpy'); empty to browse by chapter/section.")] = "",
    chapter: Annotated[int, Field(description="Chapter filter (1-21); 0 for all.")] = 0,
    section_id: Annotated[str, Field(description="Section ID filter (e.g. '5.3'); empty for all.")] = "",
    answer_filter: Annotated[
        Literal["any", "with_answer", "without_answer"],
        Field(description="Answer-key filter: 'any' (default), 'with_answer' (odd keyed), or 'without_answer' (unkeyed)."),
    ] = "any",
    limit: Annotated[int, Field(description="Max exercises per call (1-15, default 8).")] = 8,
    offset: Annotated[int, Field(description="0-based starting offset for pagination (default 0).")] = 0,
) -> str:
    """Search or browse end-of-chapter exercises by keywords, chapter, section_id, or answer-key availability, with offset pagination. Returns problems only (no answer keys)."""
    limit = max(1, min(limit, 15))
    offset = max(0, offset)
    query_clean = query.strip()
    section_clean = section_id.strip()
    chapter_filter = chapter if chapter and chapter > 0 else None
    and_q, or_q = _fts_queries(query_clean) if query_clean else ("", "")

    def _run_ex_query(fts_q: str) -> tuple[list[sqlite3.Row], int]:
        params: list[Any] = []
        if fts_q:
            from_where = """
                FROM exercises_fts f
                JOIN exercises e ON e.rowid = f.rowid
                WHERE exercises_fts MATCH ?
            """
            params.append(fts_q)
            order = "ORDER BY bm25(exercises_fts)"
        else:
            from_where = "FROM exercises e WHERE 1=1"
            order = "ORDER BY e.chapter_num, e.exercise_num"

        if chapter_filter is not None:
            from_where += " AND e.chapter_num = ?"
            params.append(chapter_filter)
        if section_clean:
            from_where += " AND e.section_id = ?"
            params.append(section_clean)
        if answer_filter == "with_answer":
            from_where += " AND e.has_answer = 1"
        elif answer_filter == "without_answer":
            from_where += " AND e.has_answer = 0"

        count_sql = f"SELECT COUNT(*) {from_where}"
        select_sql = (
            f"SELECT e.id, e.chapter_num, e.exercise_num, e.section_id, e.problem_text, e.has_answer "
            f"{from_where} {order} LIMIT ? OFFSET ?"
        )
        with _conn() as conn:
            total = conn.execute(count_sql, params).fetchone()[0]
            rows = conn.execute(select_sql, [*params, limit, offset]).fetchall()
            return rows, total

    match_mode = "filter" if not and_q else "and"
    rows, total_matching = _run_ex_query(and_q)
    if and_q and total_matching == 0 and or_q != and_q:
        rows, total_matching = _run_ex_query(or_q)
        match_mode = "or_fallback"

    next_offset = offset + len(rows)
    if rows:
        span = f"showing {offset + 1}-{next_offset} of {total_matching}"
    else:
        span = f"0 shown of {total_matching} matching"

    next_hint = ""
    if next_offset < total_matching:
        hint_args = []
        if query_clean:
            hint_args.append(f"query={query_clean!r}")
        if chapter_filter is not None:
            hint_args.append(f"chapter={chapter_filter}")
        if section_clean:
            hint_args.append(f"section_id={section_clean!r}")
        if answer_filter != "any":
            hint_args.append(f"answer_filter={answer_filter!r}")
        hint_args.append(f"offset={next_offset}")
        next_hint = f" | Next: search_exercises({', '.join(hint_args)})"

    parts = [
        f"Exercises ({span}, mode={match_mode}, answer_filter={answer_filter}{next_hint})\n"
        "Note: Answer keys omitted; call get_exercise_bundle(exercise_ids=[...], include_answers=true) after the student attempts a problem."
    ]

    if not rows:
        parts.append("No matching exercises found.")
        return "\n\n".join(parts)

    for r in rows:
        prob, _ = _cap_text(r["problem_text"], 600)
        status = "verified_official_key" if r["has_answer"] else "unavailable"
        parts.append(f"### {r['id']} (Section {r['section_id']} | key: {status})\n{prob}")

    return "\n\n".join(parts)


@mcp.tool()
def get_exercise_bundle(
    exercise_ids: Annotated[list[str], Field(description="Exercise IDs, e.g. ['Exercise 1.1', '1.2'] (max 15).")],
    include_answers: Annotated[bool, Field(description="Include official answer keys (default false). Set true only after the student attempts the problem.")] = False,
) -> str:
    """Fetch full problem statements for exercise IDs. Answer keys are hidden by default; set include_answers=true only after the student attempts the problem."""
    if not exercise_ids:
        return f"Exercise Bundle (0 found, include_answers={include_answers})\n\nNo exercise IDs provided."

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
    found_blocks: list[str] = []
    not_found: list[str] = []
    for eid in normalized:
        r = by_id.get(eid)
        if r is None:
            not_found.append(eid)
            continue
        status = "verified_official_key" if r["has_answer"] else "unavailable"
        block = f"### {r['id']} (Section {r['section_id']} | key: {status})\n{r['problem_text']}"
        if include_answers:
            ans = r["answer_text"] if r["has_answer"] else "unavailable"
            block += f"\n\n**Official Answer Key:** {ans}"
        found_blocks.append(block)

    parts = [f"Exercise Bundle ({len(found_blocks)} found, include_answers={include_answers})"]
    parts.extend(found_blocks)
    if not_found:
        parts.append(f"Not found: {', '.join(not_found)}")

    return "\n\n".join(parts)


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
    query: Annotated[str, Field(description="Formula, substance, or constant (e.g. 'NH3', 'entropy water', 'gas constant').")],
    category: Annotated[
        Literal["all", "thermodynamics", "equilibrium", "constants", "periodic_table", "general"],
        Field(description="Appendix category filter (default 'all')."),
    ] = "all",
    limit: Annotated[int, Field(description="Max tables to return (1-5, default 3).")] = 3,
) -> str:
    """Look up exact numeric data from Chemistry 2e Appendices (ΔH°f/S°/ΔG°f, Ka/Kb/Ksp, E°, constants, periodic table). Use instead of guessing values."""
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

    resolved_cat = category if category != "all" else (inferred_cat or "all")
    parts = [f'Reference Data for "{query}" (category={resolved_cat}, {len(rows)} tables)']
    if not rows:
        parts.append("No matching reference tables found.")
        return "\n\n".join(parts)

    # Filter long tables down to matching lines + header so 8B context stays clean
    row_terms = substance_terms if substance_terms else all_terms
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
        capped, _ = _cap_text(content, 2500)
        parts.append(f"## {r['title']} ({r['appendix_id']}, {r['category']})\n{capped}")

    return "\n\n".join(parts)


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
