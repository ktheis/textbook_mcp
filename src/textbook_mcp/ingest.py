"""Ingest OpenStax Chemistry 2e CNXML collection into SQLite + FTS5."""

from __future__ import annotations

import argparse
from pathlib import Path
from lxml import etree

from textbook_mcp.cnxml import (
    CNXML_NS,
    MD_NS,
    _clean_ws,
    _local,
    block_to_text,
    inline_to_text,
    table_to_markdown,
)
from textbook_mcp.db import DEFAULT_DB_PATH, get_connection, init_schema, rebuild_fts

COL_NS = "http://cnx.rice.edu/collxml"
DEFAULT_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "osbooks-chemistry-bundle"

CHUNK_TARGET_CHARS = 2200


def _categorize_appendix(title: str) -> str:
    low = title.lower()
    if "periodic" in low:
        return "periodic_table"
    if "thermodynamic" in low or "enthalpy" in low or "entropy" in low:
        return "thermodynamics"
    if "ionization" in low or "solubility" in low or "equilibrium" in low or "formation" in low or "electrode" in low:
        return "equilibrium"
    if "constant" in low or "conversion" in low or "fundamental" in low or "water" in low:
        return "constants"
    return "general"


def _parse_collection(bundle_dir: Path) -> tuple[list[str], list[tuple[int, str, list[str]]], list[str]]:
    """Return (preface_modules, [(chapter_num, title, [module_ids])], appendix_modules)."""
    coll_path = bundle_dir / "collections" / "chemistry-2e.collection.xml"
    tree = etree.parse(str(coll_path))
    root = tree.getroot()
    content = root.find(f"{{{COL_NS}}}content")
    if content is None:
        raise ValueError(f"Missing <col:content> in {coll_path}")

    preface: list[str] = []
    chapters: list[tuple[int, str, list[str]]] = []
    appendices: list[str] = []
    seen_chapters = False

    for child in content:
        tag = _local(child.tag)
        if tag == "module":
            doc_id = child.get("document")
            if doc_id:
                if not seen_chapters:
                    preface.append(doc_id)
                else:
                    appendices.append(doc_id)
        elif tag == "subcollection":
            seen_chapters = True
            title_el = child.find(f"{{{MD_NS}}}title")
            chap_title = _clean_ws(title_el.text or "") if title_el is not None else f"Chapter {len(chapters) + 1}"
            sub_content = child.find(f"{{{COL_NS}}}content")
            mod_ids: list[str] = []
            if sub_content is not None:
                for m in sub_content.findall(f"{{{COL_NS}}}module"):
                    doc_id = m.get("document")
                    if doc_id:
                        mod_ids.append(doc_id)
            chapters.append((len(chapters) + 1, chap_title, mod_ids))

    return preface, chapters, appendices


def _chunk_blocks(blocks: list[tuple[str, str]], max_chars: int = CHUNK_TARGET_CHARS) -> list[tuple[str, str]]:
    """Group (heading, block_text) pairs into chunks up to max_chars."""
    chunks: list[tuple[str, str]] = []
    cur_heading = ""
    cur_parts: list[str] = []
    cur_len = 0

    for heading, text in blocks:
        if not text:
            continue
        if cur_parts and (heading != cur_heading or cur_len + len(text) + 2 > max_chars):
            chunks.append((cur_heading, "\n\n".join(cur_parts)))
            cur_parts = []
            cur_len = 0
        cur_heading = heading
        cur_parts.append(text)
        cur_len += len(text) + 2

    if cur_parts:
        chunks.append((cur_heading, "\n\n".join(cur_parts)))
    return chunks


def _extract_module(
    bundle_dir: Path,
    module_id: str,
    section_id: str,
    chapter_num: int | None,
    counters: dict[str, int],
) -> dict:
    """Parse a single CNXML module into structured records."""
    cnxml_path = bundle_dir / "modules" / module_id / "index.cnxml"
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(cnxml_path), parser)
    root = tree.getroot()

    title_el = root.find(f"{{{CNXML_NS}}}title")
    title = _clean_ws(inline_to_text(title_el)) if title_el is not None else module_id

    # Learning objectives from <md:abstract>
    abstract_el = root.find(f".//{{{MD_NS}}}abstract")
    objectives = ""
    if abstract_el is not None:
        objectives = _clean_ws(block_to_text(abstract_el))

    content_el = root.find(f"{{{CNXML_NS}}}content")
    id_labels: dict[str, str] = {}

    figures: list[dict] = []
    tables: list[dict] = []
    examples: list[dict] = []
    exercises: list[dict] = []
    blocks: list[tuple[str, str]] = []

    if content_el is None:
        return {
            "title": title,
            "objectives": objectives,
            "chunks": [],
            "figures": [],
            "tables": [],
            "examples": [],
            "exercises": [],
        }

    # First pass: register figure/table/example labels so <link target-id="..."/> resolves cleanly
    prefix = f"{chapter_num}." if chapter_num is not None else f"{section_id}."
    for fig in content_el.findall(f".//{{{CNXML_NS}}}figure"):
        fid = fig.get("id")
        if fid:
            counters["figure"] += 1
            label = f"Figure {prefix}{counters['figure']}"
            id_labels[fid] = label

    for tbl in content_el.findall(f".//{{{CNXML_NS}}}table"):
        tid = tbl.get("id")
        if tid:
            counters["table"] += 1
            label = f"Table {prefix}{counters['table']}"
            id_labels[tid] = label

    for ex in content_el.findall(f".//{{{CNXML_NS}}}example"):
        eid = ex.get("id")
        if eid:
            counters["example"] += 1
            label = f"Example {prefix}{counters['example']}"
            id_labels[eid] = label

    # Extract figures, tables, examples metadata
    for fig in content_el.findall(f".//{{{CNXML_NS}}}figure"):
        fid = fig.get("id") or f"{module_id}-fig-{len(figures)+1}"
        label = id_labels.get(fid, f"Figure {prefix}{len(figures)+1}")
        cap_el = fig.find(f"{{{CNXML_NS}}}caption")
        caption = _clean_ws(inline_to_text(cap_el, id_labels)) if cap_el is not None else ""
        media_el = fig.find(f"{{{CNXML_NS}}}media")
        alt_text = media_el.get("alt", "") if media_el is not None else ""
        img_el = fig.find(f".//{{{CNXML_NS}}}image")
        media_src = img_el.get("src", "") if img_el is not None else ""
        if media_src.startswith("../../"):
            media_src = media_src[6:]
        figures.append({
            "id": fid,
            "section_id": section_id,
            "chapter_num": chapter_num,
            "label": label,
            "caption": caption,
            "alt_text": _clean_ws(alt_text),
            "media_path": media_src,
        })

    for tbl in content_el.findall(f".//{{{CNXML_NS}}}table"):
        tid = tbl.get("id") or f"{module_id}-tbl-{len(tables)+1}"
        label = id_labels.get(tid, f"Table {prefix}{len(tables)+1}")
        ttitle_el = tbl.find(f"{{{CNXML_NS}}}title")
        ttitle = _clean_ws(inline_to_text(ttitle_el, id_labels)) if ttitle_el is not None else ""
        tcontent = table_to_markdown(tbl, id_labels)
        tables.append({
            "id": tid,
            "section_id": section_id,
            "chapter_num": chapter_num,
            "label": label,
            "title": ttitle,
            "content": tcontent,
        })

    for ex in content_el.findall(f".//{{{CNXML_NS}}}example"):
        eid = ex.get("id") or f"{module_id}-ex-{len(examples)+1}"
        label = id_labels.get(eid, f"Example {prefix}{len(examples)+1}")
        etitle_el = ex.find(f"{{{CNXML_NS}}}title")
        etitle = _clean_ws(inline_to_text(etitle_el, id_labels)) if etitle_el is not None else ""
        econtent = block_to_text(ex, id_labels)
        examples.append({
            "id": eid,
            "section_id": section_id,
            "chapter_num": chapter_num,
            "label": label,
            "title": etitle,
            "content": econtent,
        })

    # Extract prose blocks and exercises
    def walk_content(node: etree._Element, current_heading: str) -> None:
        for child in node:
            tag = _local(child.tag)
            if tag == "section":
                sec_class = child.get("class", "")
                if "exercises" in sec_class and chapter_num is not None:
                    for ex_el in child.findall(f".//{{{CNXML_NS}}}exercise"):
                        counters["exercise"] += 1
                        ex_num = counters["exercise"]
                        prob_el = ex_el.find(f"{{{CNXML_NS}}}problem")
                        sol_el = ex_el.find(f"{{{CNXML_NS}}}solution")
                        prob_txt = block_to_text(prob_el, id_labels) if prob_el is not None else ""
                        sol_txt = block_to_text(sol_el, id_labels) if sol_el is not None else None
                        if prob_txt:
                            exercises.append({
                                "id": f"Exercise {chapter_num}.{ex_num}",
                                "chapter_num": chapter_num,
                                "exercise_num": ex_num,
                                "section_id": section_id,
                                "problem_text": prob_txt,
                                "answer_text": sol_txt if sol_txt else None,
                                "has_answer": 1 if sol_txt else 0,
                            })
                    continue

                sub_title_el = child.find(f"{{{CNXML_NS}}}title")
                sub_heading = _clean_ws(inline_to_text(sub_title_el, id_labels)) if sub_title_el is not None else current_heading
                walk_content(child, sub_heading)
            elif tag in {"title", "figure", "media"}:
                continue
            else:
                rendered = block_to_text(child, id_labels)
                if rendered:
                    blocks.append((current_heading, rendered))

    walk_content(content_el, title)
    chunks = _chunk_blocks(blocks)

    return {
        "title": title,
        "objectives": objectives,
        "chunks": chunks,
        "figures": figures,
        "tables": tables,
        "examples": examples,
        "exercises": exercises,
    }


def ingest(bundle_dir: Path = DEFAULT_BUNDLE_DIR, db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """Run full CNXML ingestion into SQLite."""
    if db_path.exists():
        db_path.unlink()

    conn = get_connection(db_path)
    init_schema(conn)

    preface, chapters, appendices = _parse_collection(bundle_dir)
    stats = {"chapters": 0, "sections": 0, "chunks": 0, "figures": 0, "tables": 0, "examples": 0, "exercises": 0, "reference_tables": 0}

    # 1. Preface
    for idx, mod_id in enumerate(preface, start=1):
        sec_id = "preface" if idx == 1 else f"preface-{idx}"
        counters = {"figure": 0, "table": 0, "example": 0, "exercise": 0}
        data = _extract_module(bundle_dir, mod_id, sec_id, None, counters)
        conn.execute(
            "INSERT INTO sections(id, chapter_num, section_num, title, module_id, kind, learning_objectives) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sec_id, None, sec_id, data["title"], mod_id, "preface", data["objectives"]),
        )
        for c_idx, (heading, content) in enumerate(data["chunks"]):
            conn.execute(
                "INSERT INTO chunks(section_id, chunk_index, heading, content, char_count) VALUES (?, ?, ?, ?, ?)",
                (sec_id, c_idx, heading, content, len(content)),
            )
            stats["chunks"] += 1
        stats["sections"] += 1

    # 2. Chapters 1..N
    for chap_num, chap_title, mod_ids in chapters:
        counters = {"figure": 0, "table": 0, "example": 0, "exercise": 0}
        intro_mod = mod_ids[0] if mod_ids else None
        sec_count = max(0, len(mod_ids) - 1)
        conn.execute(
            "INSERT INTO chapters(number, title, intro_module_id, section_count) VALUES (?, ?, ?, ?)",
            (chap_num, chap_title, intro_mod, sec_count),
        )
        stats["chapters"] += 1

        for m_idx, mod_id in enumerate(mod_ids):
            if m_idx == 0:
                sec_id = f"{chap_num}.0"
                kind = "intro"
            else:
                sec_id = f"{chap_num}.{m_idx}"
                kind = "section"

            data = _extract_module(bundle_dir, mod_id, sec_id, chap_num, counters)
            conn.execute(
                "INSERT INTO sections(id, chapter_num, section_num, title, module_id, kind, learning_objectives) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sec_id, chap_num, sec_id, data["title"], mod_id, kind, data["objectives"]),
            )
            stats["sections"] += 1

            for c_idx, (heading, content) in enumerate(data["chunks"]):
                conn.execute(
                    "INSERT INTO chunks(section_id, chunk_index, heading, content, char_count) VALUES (?, ?, ?, ?, ?)",
                    (sec_id, c_idx, heading, content, len(content)),
                )
                stats["chunks"] += 1

            for fig in data["figures"]:
                conn.execute(
                    "INSERT OR REPLACE INTO figures(id, section_id, chapter_num, label, caption, alt_text, media_path) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (fig["id"], fig["section_id"], fig["chapter_num"], fig["label"], fig["caption"], fig["alt_text"], fig["media_path"]),
                )
                stats["figures"] += 1

            for tbl in data["tables"]:
                conn.execute(
                    "INSERT OR REPLACE INTO book_tables(id, section_id, chapter_num, label, title, content) VALUES (?, ?, ?, ?, ?, ?)",
                    (tbl["id"], tbl["section_id"], tbl["chapter_num"], tbl["label"], tbl["title"], tbl["content"]),
                )
                stats["tables"] += 1

            for ex in data["examples"]:
                conn.execute(
                    "INSERT OR REPLACE INTO examples(id, section_id, chapter_num, label, title, content) VALUES (?, ?, ?, ?, ?, ?)",
                    (ex["id"], ex["section_id"], ex["chapter_num"], ex["label"], ex["title"], ex["content"]),
                )
                stats["examples"] += 1

            for exer in data["exercises"]:
                conn.execute(
                    "INSERT OR REPLACE INTO exercises(id, chapter_num, exercise_num, section_id, problem_text, answer_text, has_answer) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (exer["id"], exer["chapter_num"], exer["exercise_num"], exer["section_id"], exer["problem_text"], exer["answer_text"], exer["has_answer"]),
                )
                stats["exercises"] += 1

    # 3. Appendices -> sections + reference_tables
    for idx, mod_id in enumerate(appendices):
        letter = chr(ord("A") + idx)
        sec_id = f"app-{letter}"
        counters = {"figure": 0, "table": 0, "example": 0, "exercise": 0}
        data = _extract_module(bundle_dir, mod_id, sec_id, None, counters)
        conn.execute(
            "INSERT INTO sections(id, chapter_num, section_num, title, module_id, kind, learning_objectives) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sec_id, None, letter, data["title"], mod_id, "appendix", data["objectives"]),
        )
        stats["sections"] += 1

        category = _categorize_appendix(data["title"])
        for c_idx, (heading, content) in enumerate(data["chunks"]):
            conn.execute(
                "INSERT INTO chunks(section_id, chunk_index, heading, content, char_count) VALUES (?, ?, ?, ?, ?)",
                (sec_id, c_idx, heading, content, len(content)),
            )
            stats["chunks"] += 1

        for t_idx, tbl in enumerate(data["tables"], start=1):
            ref_id = f"{sec_id}:table-{t_idx}"
            ref_title = tbl["title"] or data["title"]
            conn.execute(
                "INSERT OR REPLACE INTO reference_tables(id, appendix_id, title, category, content) VALUES (?, ?, ?, ?, ?)",
                (ref_id, sec_id, ref_title, category, tbl["content"]),
            )
            stats["reference_tables"] += 1

        # If appendix has no formal <table> elements (e.g. prose/list constants), store chunks as reference entry
        if not data["tables"] and data["chunks"]:
            combined = "\n\n".join(c for _, c in data["chunks"])
            conn.execute(
                "INSERT OR REPLACE INTO reference_tables(id, appendix_id, title, category, content) VALUES (?, ?, ?, ?, ?)",
                (f"{sec_id}:text", sec_id, data["title"], category, combined),
            )
            stats["reference_tables"] += 1

    conn.commit()
    rebuild_fts(conn)
    conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest OpenStax Chemistry 2e CNXML into SQLite.")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE_DIR, help="Path to osbooks-chemistry-bundle")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Output SQLite DB path")
    args = parser.parse_args()

    stats = ingest(args.bundle, args.db)
    print("Ingestion complete:", stats)


if __name__ == "__main__":
    main()
