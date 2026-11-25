## General Chemistry 2e Local Tool Server — Top-Level Specification

### 1. Purpose & Scope

This server acts as a deterministic, local retrieval and verification layer between an 8B-parameter language model and a curated knowledge base built around **OpenStax Chemistry 2e** and supplementary reference corpora. Its primary objectives are:

1. Compensating for an 8B model's parametric memory limits and mathematical/sign-convention fragility during conceptual Q&A.
2. Providing structured, verifiable access to end-of-chapter exercises (paired with odd-numbered answer keys) so the model/harness can assemble practice problem sets without inventing flawed questions or hallucinating solutions.

---

### 2. Core Corpora & Data Model

The server manages four distinct, read-only knowledge stores:

* **Structural Map (Always-Available Index):** A compact representation of the textbook's hierarchy (Chapters, Sections, canonical OpenStax page spans, and numbered Figures/Tables/Examples with captions) designed to fit within the model's baseline context budget.
* **Textbook Exposition Store:** Cleaned, section-chunked instructional prose, worked examples, key equations, and "Check Your Learning" items, tagged with canonical OpenStax chapter, section, page range, and figure/table identifiers.
* **Exercise & Answer Key Bank:** End-of-chapter problems extracted into discrete, queryable records. Each record links:
* Problem ID (chapter and problem number)
* Mapped textbook section(s) and topic tags
* Full problem statement (including referenced figures/tables)
* Official answer key entry (populated for odd-numbered problems; explicitly marked `unavailable` for even-numbered problems)


* **Supplementary Reference Store:** Offline general-science reference material (e.g., text-only Wikipedia snapshot) plus deterministic reference tables (periodic table properties, standard thermodynamic values $\Delta H^\circ_f, \Delta G^\circ_f, S^\circ$, $K_a/K_b/K_{sp}$ tables, physical constants with units) to prevent lookup hallucinations.

---

### 3. Functional Capabilities (Tool Surface)

The server exposes a minimal, high-signal set of tools to the LLM harness:

1. **Structural Navigation (`get_toc` / `browse_chapter`):** Allows the model to inspect chapter outlines, section boundaries, and figure/table lists before executing targeted reads.
2. **Exposition Retrieval (`search_textbook`, `read_section`):** Supports both keyword/phrase search and direct retrieval by section ID, returning cleaned text with explicit canonical page and figure citations.
3. **Problem Set Querying (`search_exercises`, `get_exercise_bundle`):** Filters end-of-chapter exercises by chapter, section, keyword, or answer-key availability (`has_answer=True/False`). Returns problem text alongside its paired answer key entry (when available) so the model can build practice sets with verified answers or assign unkeyed problems for reflection.
4. **Reference & Constant Lookup (`lookup_reference_data`, `search_background`):** Queries deterministic chemical data tables (constants, molar masses, thermodynamic/equilibrium values) and offline encyclopedia articles for prerequisite concepts outside the textbook.
5. **Deterministic Computation / Verification (`evaluate_expression` — optional/recommended):** Offloads numerical stoichiometry, unit conversions, and equilibrium algebra from the 8B model to a deterministic solver so numerical outputs are exact.

---

### 4. Non-Functional & Pedagogical Requirements

* **Citation Integrity:** Every returned textbook passage, figure reference, or exercise must carry canonical OpenStax identifiers (e.g., `Section 6.5, p. 291`, `Figure 6.32`, `Exercise 6.75`) so the model cites locations students can directly verify in their copy.
* **Answer-Key Guardrails:** The server must cleanly distinguish between verified textbook answers (odd-numbered exercises) and unkeyed exercises (even-numbered), preventing the model from presenting improvised 8B math as an official textbook answer.
* **Low Latency & Local Portability:** Queries must execute in milliseconds on consumer laptop hardware without requiring a dedicated GPU or internet access.
* **Provenance & Auditability:** All tool invocations, query arguments, and returned payloads must be structured so the host harness can log or display exact sources consulted for any student interaction.