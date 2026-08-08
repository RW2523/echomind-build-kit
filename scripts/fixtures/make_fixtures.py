"""Generate the demo fixtures (run once; the outputs are committed).

    python -m scripts.fixtures.make_fixtures

The PDF is written by hand rather than with a PDF library: the demo needs one small,
stable, text-extractable file, and that is not worth a dependency that only this script
would use.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

FORM_LINES = [
    "GENOMICS CORE - BULK RNA-SEQ SUBMISSION FORM",
    "",
    "Submitted by:      Alice Nguyen  (u-alice)",
    "Lab:               Patel Lab (Lab A)",
    "Account code:      ACC-A1",
    "Template:          tpl-rna-seq",
    "",
    "FIELD VALUES",
    "",
    "sample_count:      12",
    "organism:          Mus musculus",
    "read_length:       150bp",
    "notes:             Hypoxia timecourse, fixation-window replicates.",
    "",
    "Signed: A. Nguyen        Date: 2026-03-30",
]


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF with a text content stream."""
    content_parts = ["BT", "/F1 11 Tf", "14 TL", "56 760 Td"]
    for line in lines:
        content_parts.append(f"({_escape(line)}) Tj")
        content_parts.append("T*")
    content_parts.append("ET")
    content = "\n".join(content_parts).encode("latin-1")

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n".encode()
        + b"%%EOF\n"
    )
    return bytes(out)


PRIVATE_NOTE = """---
title: Alice — Hypoxia Timecourse Working Note
version: "1.0"
---

# Hypoxia Timecourse — Working Note

Private to me. Uploaded to my own space, not shared with the lab.

## Verification marker

The private marker for this note is **ORRERY-3187**. It appears in no other document.
If another user's search ever returns it, the permission filter has failed.

## Where I am

The 12-minute fixation condition shows the reorganisation clearly; the 8-minute condition
barely shows it at all. That ordering is backwards from what the hypoxia model predicts,
which is why I now think a good part of the effect is a fixation artefact.

## Next

Repeat the 8-minute condition on the Confocal C2 specifically, so the detector is not a
confounder, before I show any of this to the lab.
"""


def main() -> None:
    pdf_path = HERE / "rna-seq-submission-form.pdf"
    pdf_path.write_bytes(build_pdf(FORM_LINES))

    note_path = HERE / "private-note.md"
    note_path.write_text(PRIVATE_NOTE, encoding="utf-8")

    # Prove the PDF is extractable — a fixture that pypdf cannot read is useless.
    from pypdf import PdfReader

    text = (PdfReader(str(pdf_path)).pages[0].extract_text() or "")
    missing = [t for t in ("tpl-rna-seq", "12", "Mus musculus", "150bp") if t not in text]
    if missing:
        raise SystemExit(f"PDF fixture is not extractable; missing {missing}")

    print(f"wrote {pdf_path.name} ({pdf_path.stat().st_size} bytes, text extraction OK)")
    print(f"wrote {note_path.name} ({note_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
