"""Render a generated document to DOCX or PDF.

The three templates in `actions.py` build Markdown, which was the whole output format:
a facility admin who asks for a monthly summary wants something they can attach to an
email, not a .md file. The renderers are unchanged — they still return Markdown, still
build every value from a query — and this turns that one representation into the two
formats people actually send.

Deliberately not a general Markdown engine. It handles exactly what the templates emit —
headings, pipe tables, bullets, `**bold**`, `inline code`, and the two-space line break —
and anything else passes through as plain text. A general parser would be a dependency
and a surface area for no gain, since the input is ours.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.shared import Pt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FORMATS = ("md", "docx", "pdf")
EXTENSION = {"md": ".md", "docx": ".docx", "pdf": ".pdf"}

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(text: str) -> str:
    """Strip the inline markers, for renderers that cannot show them."""
    return _CODE_RE.sub(r"\1", _BOLD_RE.sub(r"\1", text)).strip()


def _blocks(markdown: str) -> list[tuple[str, object]]:
    """Parse into ("heading"|"table"|"bullet"|"para", payload).

    One pass, because a pipe table has to be gathered across lines before it can be
    emitted, and everything else is line-at-a-time.
    """
    out: list[tuple[str, object]] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            out.append(("heading", (len(heading.group(1)), _plain(heading.group(2)))))
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _cells(stripped)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([_plain(c) for c in _cells(lines[i])])
                i += 1
            out.append(("table", ([_plain(h) for h in header], rows)))
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            out.append(("bullet", _plain(bullet.group(1))))
            i += 1
            continue

        out.append(("para", stripped))
        i += 1
    return out


def to_docx(title: str, markdown: str) -> bytes:
    doc = Document()
    doc.core_properties.title = title

    for kind, payload in _blocks(markdown):
        if kind == "heading":
            level, text = payload
            doc.add_heading(text, level=min(level, 4))
        elif kind == "bullet":
            doc.add_paragraph(str(payload), style="List Bullet")
        elif kind == "table":
            header, rows = payload
            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Light Grid Accent 1"
            for cell, text in zip(table.rows[0].cells, header, strict=True):
                cell.text = text
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            for row in rows:
                cells = table.add_row().cells
                # Templates can emit a short row (the "no usage recorded" placeholder),
                # so pad rather than let zip(strict=True) raise on a document that is
                # otherwise perfectly valid.
                for cell, text in zip(cells, row + [""] * (len(header) - len(row)), strict=False):
                    cell.text = text
        else:
            para = doc.add_paragraph()
            _add_runs(para, str(payload))

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _add_runs(paragraph, text: str) -> None:
    """Keep bold and code visibly distinct instead of flattening the markers away."""
    position = 0
    for match in re.finditer(r"\*\*(.+?)\*\*|`([^`]+)`", text):
        if match.start() > position:
            paragraph.add_run(text[position : match.start()])
        if match.group(1) is not None:
            paragraph.add_run(match.group(1)).bold = True
        else:
            run = paragraph.add_run(match.group(2))
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        position = match.end()
    if position < len(text):
        paragraph.add_run(text[position:])


def to_pdf(title: str, markdown: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=title,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    story: list[object] = []

    for kind, payload in _blocks(markdown):
        if kind == "heading":
            level, text = payload
            style = styles["Title"] if level == 1 else styles[f"Heading{min(level, 4)}"]
            story += [Paragraph(_escape(text), style), Spacer(1, 4)]
        elif kind == "bullet":
            story.append(Paragraph(f"• {_escape(str(payload))}", body))
        elif kind == "table":
            header, rows = payload
            data = [[Paragraph(f"<b>{_escape(h)}</b>", body) for h in header]]
            for row in rows:
                padded = row + [""] * (len(header) - len(row))
                data.append([Paragraph(_escape(c), body) for c in padded])
            table = Table(data, hAlign="LEFT", repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3dd")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story += [Spacer(1, 4), table, Spacer(1, 6)]
        else:
            story.append(Paragraph(_inline_to_html(str(payload)), body))

    doc.build(story)
    return buffer.getvalue()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_to_html(text: str) -> str:
    """reportlab paragraphs take a small HTML dialect, so translate rather than strip."""
    escaped = _escape(text)
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    return _CODE_RE.sub(r'<font face="Courier">\1</font>', escaped)


def render(title: str, markdown: str, fmt: str) -> bytes:
    if fmt == "md":
        return markdown.encode("utf-8")
    if fmt == "docx":
        return to_docx(title, markdown)
    if fmt == "pdf":
        return to_pdf(title, markdown)
    raise ValueError(f"unsupported format {fmt!r}; expected one of {FORMATS}")
