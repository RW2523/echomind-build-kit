"""Heading-aware chunking: 300–500 tokens, 40-token overlap, tables kept whole.

Token counts are estimated from whitespace words rather than measured with the model's
own tokenizer: the OpenAI-compatible /embeddings API exposes no tokenizer, and importing
one for a different model would be a worse approximation than an honest heuristic. The
300–500 band is a sizing target, not a hard model limit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

TARGET_MIN = 300
TARGET_MAX = 500
OVERLAP = 40
TOKENS_PER_WORD = 1.33

# A heading boundary near the end of a document can leave a 30–50 token remainder, which
# is too small to retrieve or cite usefully. Fragments below this are folded back into the
# preceding chunk, provided the result stays within MERGE_CEILING.
MIN_STANDALONE = 150
MERGE_CEILING = 650

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|")


@dataclass
class Chunk:
    ord: int
    text: str
    breadcrumb: str


def estimate_tokens(text: str) -> int:
    words = len(text.split())
    return math.ceil(words * TOKENS_PER_WORD)


def _tail_words(text: str, tokens: int) -> str:
    """The last ~`tokens` tokens of text, for overlap."""
    words = text.split()
    take = max(1, int(tokens / TOKENS_PER_WORD))
    return " ".join(words[-take:]) if words else ""


@dataclass
class _Block:
    text: str
    breadcrumb: str
    atomic: bool = False  # tables and code fences are never split


def _blocks(markdown: str, title: str, version: str) -> list[_Block]:
    """Split into paragraph/table/code blocks, each tagged with its heading breadcrumb."""
    lines = markdown.splitlines()
    headings: dict[int, str] = {}
    out: list[_Block] = []
    buf: list[str] = []
    mode = "text"  # text | table | code

    def breadcrumb() -> str:
        parts = [title]
        for level in (2, 3, 4):
            if headings.get(level):
                parts.append(headings[level])
        return f"{' > '.join(parts)} (v{version})"

    def flush(atomic: bool = False) -> None:
        nonlocal buf
        body = "\n".join(buf).strip()
        if body:
            out.append(_Block(body, breadcrumb(), atomic))
        buf = []

    for line in lines:
        if line.strip().startswith("```"):
            if mode == "code":
                buf.append(line)
                flush(atomic=True)
                mode = "text"
            else:
                flush()
                buf.append(line)
                mode = "code"
            continue
        if mode == "code":
            buf.append(line)
            continue

        heading = HEADING_RE.match(line)
        if heading:
            flush(atomic=(mode == "table"))
            mode = "text"
            level = len(heading.group(1))
            headings[level] = heading.group(2)
            # A new heading invalidates deeper levels.
            for deeper in list(headings):
                if deeper > level:
                    headings.pop(deeper)
            continue

        is_table_row = bool(TABLE_ROW_RE.match(line))
        if is_table_row and mode != "table":
            flush()
            mode = "table"
        elif not is_table_row and mode == "table":
            flush(atomic=True)
            mode = "text"

        if not line.strip():
            if mode != "table":
                flush()
            continue
        buf.append(line)

    flush(atomic=(mode == "table"))
    return out


def chunk_markdown(markdown: str, title: str, version: str) -> list[Chunk]:
    blocks = _blocks(markdown, title, version)
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_tokens = 0
    cur_crumb = ""

    def flush() -> None:
        nonlocal cur, cur_tokens
        body = "\n\n".join(cur).strip()
        if body:
            chunks.append(Chunk(ord=len(chunks), text=body, breadcrumb=cur_crumb))
        cur, cur_tokens = [], 0

    for block in blocks:
        btokens = estimate_tokens(block.text)

        # Heading change is a natural boundary once the chunk is big enough to stand alone.
        if cur and block.breadcrumb != cur_crumb and cur_tokens >= TARGET_MIN:
            flush()

        if cur and cur_tokens + btokens > TARGET_MAX:
            tail = _tail_words("\n\n".join(cur), OVERLAP)
            flush()
            if tail:
                cur, cur_tokens = [tail], estimate_tokens(tail)

        if not cur:
            cur_crumb = block.breadcrumb

        # An atomic block larger than the target still goes in whole — a split table is
        # worse than an oversized chunk.
        cur.append(block.text)
        cur_tokens += btokens

        if cur_tokens >= TARGET_MAX:
            flush()

    flush()
    chunks = _absorb_fragments(chunks)
    for i, c in enumerate(chunks):
        c.ord = i
    return chunks


def _absorb_fragments(chunks: list[Chunk]) -> list[Chunk]:
    """Fold undersized trailing fragments back into the chunk before them."""
    merged: list[Chunk] = []
    for chunk in chunks:
        if (
            merged
            and estimate_tokens(chunk.text) < MIN_STANDALONE
            and estimate_tokens(merged[-1].text) + estimate_tokens(chunk.text) <= MERGE_CEILING
        ):
            previous = merged[-1]
            merged[-1] = Chunk(
                ord=previous.ord,
                text=f"{previous.text}\n\n{chunk.text}",
                # Keep the earlier breadcrumb: it names the section the reader lands in.
                breadcrumb=previous.breadcrumb,
            )
            continue
        merged.append(chunk)
    return merged


def chunk_pdf(path: str, title: str, version: str) -> list[Chunk]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for n, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"## Page {n}\n\n{text}")
    return chunk_markdown("\n\n".join(pages), title, version)
