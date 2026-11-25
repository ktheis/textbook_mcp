"""CNXML and MathML-to-text/LaTeX conversion utilities for OpenStax Chemistry 2e."""

from __future__ import annotations

import re
from lxml import etree

CNXML_NS = "http://cnx.rice.edu/cnxml"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
MD_NS = "http://cnx.rice.edu/mdml"

_SUBSCRIPT_MAP = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SUPERSCRIPT_MAP = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _clean_ws(text: str) -> str:
    text = re.sub(r"[ \t\r\n]+", " ", text)
    return text.strip()


def mathml_to_text(elem: etree._Element) -> str:
    """Convert a MathML subtree into compact, readable Unicode/LaTeX-friendly math."""
    tag = _local(elem.tag)

    if tag in {"math", "mrow", "mstyle", "mpadded", "mphantom", "mfenced"}:
        parts = [mathml_to_text(c) for c in elem]
        joined = "".join(p for p in parts if p)
        if tag == "mfenced":
            op = elem.get("open", "(")
            cl = elem.get("close", ")")
            return f"{op}{joined}{cl}"
        return joined

    if tag in {"mi", "mn", "mo", "mtext", "ms"}:
        txt = _clean_ws("".join(elem.itertext()))
        if tag == "mo" and txt in {"=", "+", "−", "-", "×", "·", "→", "⇌", "<", ">", "≤", "≥", "≈"}:
            return f" {txt} "
        return txt

    if tag == "mspace":
        return " "

    if tag == "msub":
        children = list(elem)
        if len(children) >= 2:
            base = mathml_to_text(children[0])
            sub = mathml_to_text(children[1]).strip()
            if sub and all(ch in "0123456789+-=()" for ch in sub):
                return f"{base}{sub.translate(_SUBSCRIPT_MAP)}"
            return f"{base}_{{{sub}}}"

    if tag == "msup":
        children = list(elem)
        if len(children) >= 2:
            base = mathml_to_text(children[0])
            sup = mathml_to_text(children[1]).strip()
            if sup and all(ch in "0123456789+-=()" for ch in sup):
                return f"{base}{sup.translate(_SUPERSCRIPT_MAP)}"
            return f"{base}^{{{sup}}}"

    if tag == "msubsup":
        children = list(elem)
        if len(children) >= 3:
            base = mathml_to_text(children[0])
            sub = mathml_to_text(children[1]).strip()
            sup = mathml_to_text(children[2]).strip()
            return f"{base}_{{{sub}}}^{{{sup}}}"

    if tag == "mfrac":
        children = list(elem)
        if len(children) >= 2:
            num = mathml_to_text(children[0]).strip()
            den = mathml_to_text(children[1]).strip()
            return f"({num})/({den})"

    if tag == "msqrt":
        inner = "".join(mathml_to_text(c) for c in elem).strip()
        return f"√({inner})"

    if tag == "mroot":
        children = list(elem)
        if len(children) >= 2:
            rad = mathml_to_text(children[0]).strip()
            idx = mathml_to_text(children[1]).strip()
            return f"{idx}√({rad})"

    if tag == "mover":
        children = list(elem)
        if len(children) >= 2:
            return mathml_to_text(children[0])

    if tag == "munder":
        children = list(elem)
        if len(children) >= 2:
            return f"{mathml_to_text(children[0])} ({mathml_to_text(children[1])})"

    if tag == "mtable":
        rows = []
        for tr in elem:
            cells = [mathml_to_text(td).strip() for td in tr]
            rows.append(" | ".join(cells))
        return " ; ".join(rows)

    return "".join(mathml_to_text(c) for c in elem)


def inline_to_text(elem: etree._Element, id_labels: dict[str, str] | None = None) -> str:
    """Recursively render inline CNXML content to clean text with Unicode sub/superscripts."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)

    for child in elem:
        tag = _local(child.tag)
        if tag == "math" or child.tag.startswith(f"{{{MATHML_NS}}}"):
            parts.append(mathml_to_text(child))
        elif tag == "sub":
            inner = inline_to_text(child, id_labels).strip()
            if inner and all(ch in "0123456789+-=()" for ch in inner):
                parts.append(inner.translate(_SUBSCRIPT_MAP))
            else:
                parts.append(f"_{{{inner}}}")
        elif tag == "sup":
            inner = inline_to_text(child, id_labels).strip()
            if inner and all(ch in "0123456789+-=()" for ch in inner):
                parts.append(inner.translate(_SUPERSCRIPT_MAP))
            else:
                parts.append(f"^{{{inner}}}")
        elif tag == "link":
            target = child.get("target-id", "")
            inner = inline_to_text(child, id_labels).strip()
            if inner:
                parts.append(inner)
            elif id_labels and target in id_labels:
                parts.append(id_labels[target])
            elif target:
                parts.append(target)
        elif tag == "newline":
            parts.append("\n")
        elif tag in {"figure", "table", "media"}:
            # Handled separately at block level
            pass
        else:
            parts.append(inline_to_text(child, id_labels))

        if child.tail:
            parts.append(child.tail)

    return "".join(parts)


def table_to_markdown(table_elem: etree._Element, id_labels: dict[str, str] | None = None) -> str:
    """Render a CNXML <table> element into compact Markdown."""
    lines: list[str] = []
    title_el = table_elem.find(f"{{{CNXML_NS}}}title")
    if title_el is not None:
        title_txt = _clean_ws(inline_to_text(title_el, id_labels))
        if title_txt:
            lines.append(f"**{title_txt}**")

    tgroup = table_elem.find(f"{{{CNXML_NS}}}tgroup")
    if tgroup is None:
        return "\n".join(lines)

    for group_tag in ("thead", "tbody"):
        group = tgroup.find(f"{{{CNXML_NS}}}{group_tag}")
        if group is None:
            continue
        rows = group.findall(f"{{{CNXML_NS}}}row")
        for idx, row in enumerate(rows):
            entries = [
                _clean_ws(inline_to_text(entry, id_labels)).replace("|", "\\|")
                for entry in row.findall(f"{{{CNXML_NS}}}entry")
            ]
            if not entries:
                continue
            lines.append("| " + " | ".join(entries) + " |")
            if group_tag == "thead" and idx == len(rows) - 1:
                lines.append("| " + " | ".join(["---"] * len(entries)) + " |")

    return "\n".join(lines)


def block_to_text(elem: etree._Element, id_labels: dict[str, str] | None = None) -> str:
    """Render a block-level CNXML element (para, list, equation, note, example, etc.) to text."""
    tag = _local(elem.tag)

    if tag == "para":
        txt = _clean_ws(inline_to_text(elem, id_labels))
        return txt

    if tag == "list":
        items = []
        list_type = elem.get("list-type", "bulleted")
        for i, item in enumerate(elem.findall(f"{{{CNXML_NS}}}item"), start=1):
            item_txt = _clean_ws(inline_to_text(item, id_labels))
            if item_txt:
                prefix = f"{i}." if list_type == "enumerated" else "-"
                items.append(f"{prefix} {item_txt}")
        return "\n".join(items)

    if tag == "equation":
        math_el = elem.find(f".//{{{MATHML_NS}}}math")
        if math_el is not None:
            return f"  {mathml_to_text(math_el)}"
        return _clean_ws(inline_to_text(elem, id_labels))

    if tag == "table":
        return table_to_markdown(elem, id_labels)

    if tag in {"note", "example"}:
        parts = []
        title_el = elem.find(f"{{{CNXML_NS}}}title")
        if title_el is not None:
            parts.append(f"[{_clean_ws(inline_to_text(title_el, id_labels))}]")
        for child in elem:
            ctag = _local(child.tag)
            if ctag == "title":
                continue
            rendered = block_to_text(child, id_labels)
            if rendered:
                parts.append(rendered)
        return "\n".join(parts)

    # Generic container (e.g., problem, solution, section)
    parts = []
    for child in elem:
        ctag = _local(child.tag)
        if ctag in {"title", "figure", "media"}:
            continue
        rendered = block_to_text(child, id_labels)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)
