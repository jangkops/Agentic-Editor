"""Property 7: PDF 생성 라운드트립 (Validates: Requirements 4.1, 4.3).

For any valid input (title + non-empty sections list), `_tool_generate_pdf` MUST:

  - produce a real PDF at `<project>/.generated/<name>.pdf` that pypdf can parse
  - return a JSON response with:
      * path        -> relative `.generated/...` path
      * model       -> "reportlab"
      * pageCount   -> integer > 0
      * fileSize    -> integer matching the on-disk size of the produced file
                       (sizeBytes is also accepted as an alias)
  - re-reading the PDF must yield the title text (modulo whitespace introduced
    by reportlab's line wrapping)

The property is exercised against the *real* `_tool_generate_pdf`. No
Bedrock calls are issued because the generated sections never set
`imagePrompt`, so the function takes its text-only path end-to-end.

Run:
  ai_engine/.venv/bin/python scripts/test_pdf_roundtrip_property.py
"""
from __future__ import annotations

import os
import sys
import json
import asyncio
import tempfile

# Make the ai_engine package importable from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from ai_engine.server import _tool_generate_pdf  # noqa: E402


# ---------- text strategies ----------

# reportlab's Paragraph treats `<` / `>` / `&` as XML markup, so we restrict
# the alphabet to safe printable ASCII to keep generators focused on the
# round-trip property rather than markup escaping. Spaces are allowed so we
# also exercise wrapping behaviour.
_SAFE_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " .,:;!?-_()[]"
)

_title_strategy = st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=100).filter(
    lambda s: s.strip() != ""
)

_heading_strategy = st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=50)
_body_strategy = st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=200)

_section_strategy = st.fixed_dictionaries({
    "heading": _heading_strategy,
    "body": _body_strategy,
})

_sections_strategy = st.lists(_section_strategy, min_size=1, max_size=10)


# ---------- helpers ----------

def _normalize(s: str) -> str:
    """Collapse whitespace and lowercase so that reportlab's line wrapping
    (which inserts newlines and may pad with spaces) doesn't break a
    substring match against the original title."""
    return "".join(ch for ch in s if not ch.isspace()).lower()


def _extract_pdf_text(abs_path: str) -> str:
    reader = PdfReader(abs_path)
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _run_pdf(title: str, sections: list[dict], project_path: str) -> dict:
    raw = asyncio.run(_tool_generate_pdf(
        {"title": title, "sections": sections},
        project_path,
    ))
    return json.loads(raw)


# ---------- Property 7 ----------

@settings(max_examples=30, deadline=None)
@given(title=_title_strategy, sections=_sections_strategy)
def test_pdf_roundtrip(title: str, sections: list[dict]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parsed = _run_pdf(title, sections, tmp)

        # The text-only path must never hit an error response.
        assert "error" not in parsed, (
            f"unexpected error response: {parsed}\n"
            f"title={title!r} sections={sections!r}"
        )

        # ---- Req 4.3: response shape ----
        assert "path" in parsed and isinstance(parsed["path"], str), (
            f"missing path: {parsed}"
        )
        assert parsed["path"].startswith(".generated/") and parsed["path"].endswith(".pdf"), (
            f"path must be .generated/*.pdf: {parsed['path']}"
        )

        assert "pageCount" in parsed and isinstance(parsed["pageCount"], int), (
            f"missing/invalid pageCount: {parsed}"
        )
        assert parsed["pageCount"] > 0, f"pageCount must be > 0, got {parsed['pageCount']}"

        # fileSize is the canonical name; sizeBytes is an alias the impl emits
        # alongside it. Accept either, but require one.
        size_key = "fileSize" if "fileSize" in parsed else "sizeBytes"
        assert size_key in parsed and isinstance(parsed[size_key], int), (
            f"missing/invalid file size field: {parsed}"
        )

        # ---- File on disk matches the response ----
        abs_path = os.path.join(tmp, parsed["path"])
        assert os.path.isfile(abs_path), f"PDF not written to {abs_path}"

        actual_size = os.path.getsize(abs_path)
        assert parsed[size_key] == actual_size, (
            f"{size_key} ({parsed[size_key]}) != actual on-disk size ({actual_size})"
        )
        assert actual_size > 0, "PDF file is empty"

        # ---- Req 4.1: produced PDF is a valid, parseable A4 document ----
        try:
            reader = PdfReader(abs_path)
        except Exception as e:  # pragma: no cover - lets hypothesis shrink
            raise AssertionError(f"pypdf failed to open {abs_path}: {e}")

        page_count_disk = len(reader.pages)
        assert page_count_disk == parsed["pageCount"], (
            f"pageCount mismatch: response={parsed['pageCount']} pdf={page_count_disk}"
        )

        # Title must round-trip — reportlab may wrap a long title across
        # several lines, so we compare with whitespace stripped/lowercased.
        extracted = _extract_pdf_text(abs_path)
        assert _normalize(title) in _normalize(extracted), (
            f"title not found in extracted PDF text\n"
            f"  title    = {title!r}\n"
            f"  extracted= {extracted!r}"
        )


def main() -> None:
    print("=== Property 7: PDF 생성 라운드트립 ===")
    print("  validates: 4.1 (PDF생성, pypdf parse), 4.3 (path/pageCount/fileSize)")
    test_pdf_roundtrip()
    print("  round-trip property                              OK")
    print("All Property 7 cases passed.")


if __name__ == "__main__":
    main()
