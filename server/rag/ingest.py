"""Ingestion CLI and the shared ingest pipeline.

    python -m server.rag.ingest <path> --visibility public|lab|private
        [--lab LAB_ID] [--owner USER_ID] [--facility FACILITY_ID] --title T --version V

A directory ingests every .md/.pdf inside it, reading per-file front matter for the
metadata so the sample corpus can describe its own visibility.

UI uploads call `ingest_text` / `ingest_file` directly with visibility='private' and
owner_user_id=caller — the same pipeline, not a parallel one.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from server.db import session_scope
from server.rag.chunker import Chunk, chunk_markdown, chunk_pdf
from server.rag.embeddings import embed, to_pgvector

log = logging.getLogger("echomind.ingest")

VISIBILITIES = ("public", "lab", "private")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def doc_id_for(title: str, version: str) -> str:
    return f"doc-{slug(title)}-v{slug(version)}"


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """Minimal `key: value` YAML-ish front matter. No dependency, no surprises."""
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, raw[match.end():]


def _store(
    *,
    title: str,
    version: str,
    visibility: str,
    chunks: list[Chunk],
    owner_user_id: str | None,
    lab_id: str | None,
    facility_id: str | None,
    source_path: str | None,
) -> dict[str, Any]:
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {VISIBILITIES}")
    if visibility == "lab" and not lab_id:
        raise ValueError("visibility='lab' requires --lab")
    if visibility == "private" and not owner_user_id:
        raise ValueError("visibility='private' requires --owner")

    doc_id = doc_id_for(title, version)
    vectors = embed([c.text for c in chunks])

    with session_scope() as s:
        # Re-ingesting the same title+version replaces its chunks...
        s.execute(text("DELETE FROM echomind.chunks WHERE doc_id = :id"), {"id": doc_id})
        # ...and a new version expires every older version's chunks, so retrieval only
        # ever sees the current one. The old doc rows stay as provenance tombstones.
        s.execute(
            text(
                """DELETE FROM echomind.chunks
                   WHERE doc_id IN (SELECT id FROM echomind.knowledge_docs
                                    WHERE title = :title AND id <> :id)"""
            ),
            {"title": title, "id": doc_id},
        )
        s.execute(
            text(
                """INSERT INTO echomind.knowledge_docs
                       (id, title, version, visibility, owner_user_id, lab_id,
                        facility_id, source_path, updated_at)
                   VALUES (:id, :title, :version, :visibility, :owner, :lab,
                           :facility, :src, now())
                   ON CONFLICT (id) DO UPDATE SET
                       title = EXCLUDED.title, version = EXCLUDED.version,
                       visibility = EXCLUDED.visibility,
                       owner_user_id = EXCLUDED.owner_user_id,
                       lab_id = EXCLUDED.lab_id, facility_id = EXCLUDED.facility_id,
                       source_path = EXCLUDED.source_path, updated_at = now()"""
            ),
            {
                "id": doc_id, "title": title, "version": version, "visibility": visibility,
                "owner": owner_user_id, "lab": lab_id, "facility": facility_id,
                "src": source_path,
            },
        )
        s.execute(
            text(
                """INSERT INTO echomind.chunks
                       (doc_id, ord, text, breadcrumb, embedding, visibility,
                        owner_user_id, lab_id, facility_id)
                   VALUES (:doc_id, :ord, :text, :breadcrumb, CAST(:embedding AS vector),
                           :visibility, :owner, :lab, :facility)"""
            ),
            [
                {
                    "doc_id": doc_id, "ord": c.ord, "text": c.text,
                    "breadcrumb": c.breadcrumb, "embedding": to_pgvector(v),
                    "visibility": visibility, "owner": owner_user_id,
                    "lab": lab_id, "facility": facility_id,
                }
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )

    log.info("ingested %s (%s) — %d chunks, visibility=%s", title, version, len(chunks), visibility)
    return {"doc_id": doc_id, "title": title, "version": version,
            "visibility": visibility, "chunks": len(chunks)}


def ingest_text(
    body: str, *, title: str, version: str = "1", visibility: str = "public",
    owner_user_id: str | None = None, lab_id: str | None = None,
    facility_id: str | None = None, source_path: str | None = None,
) -> dict[str, Any]:
    chunks = chunk_markdown(body, title, version)
    if not chunks:
        raise ValueError(f"{title!r} produced no chunks — is the document empty?")
    return _store(
        title=title, version=version, visibility=visibility, chunks=chunks,
        owner_user_id=owner_user_id, lab_id=lab_id, facility_id=facility_id,
        source_path=source_path,
    )


def ingest_file(path: Path, **overrides: Any) -> dict[str, Any]:
    """Ingest one .md or .pdf. Front matter supplies defaults; overrides win."""
    suffix = path.suffix.lower()
    if suffix not in (".md", ".markdown", ".pdf"):
        raise ValueError(f"unsupported file type: {path.name}")

    meta: dict[str, Any] = {}
    if suffix == ".pdf":
        title = overrides.get("title") or path.stem.replace("-", " ").title()
        version = str(overrides.get("version") or "1")
        chunks = chunk_pdf(str(path), title, version)
    else:
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_front_matter(raw)
        title = overrides.get("title") or meta.get("title") or path.stem.replace("-", " ").title()
        version = str(overrides.get("version") or meta.get("version") or "1")
        chunks = chunk_markdown(body, title, version)

    def pick(key: str, meta_key: str | None = None):
        value = overrides.get(key)
        return value if value not in (None, "") else meta.get(meta_key or key) or None

    if not chunks:
        raise ValueError(f"{path.name} produced no chunks")

    return _store(
        title=title,
        version=version,
        visibility=pick("visibility") or "public",
        chunks=chunks,
        owner_user_id=pick("owner_user_id", "owner"),
        lab_id=pick("lab_id", "lab"),
        facility_id=pick("facility_id", "facility"),
        source_path=str(path),
    )


def list_user_docs(owner_user_id: str) -> list[dict[str, Any]]:
    """A user's private uploads with their chunk counts.

    Lives here rather than in the API layer so that the chunks table stays reachable from
    exactly two places: this module writes it, retrieval reads it for answering.
    """
    with session_scope() as s:
        rows = s.execute(
            text(
                """SELECT d.id, d.title, d.version, d.updated_at,
                          (SELECT count(*) FROM echomind.chunks c WHERE c.doc_id = d.id)
                              AS chunks
                   FROM echomind.knowledge_docs d
                   WHERE d.visibility = 'private' AND d.owner_user_id = :uid
                   ORDER BY d.updated_at DESC"""
            ),
            {"uid": owner_user_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def delete_doc(doc_id: str, owner_user_id: str | None = None) -> bool:
    """Hard-delete a doc and its chunks. If owner_user_id is given, only that user's."""
    with session_scope() as s:
        result = s.execute(
            text(
                """DELETE FROM echomind.knowledge_docs
                   WHERE id = :id
                     AND (CAST(:owner AS text) IS NULL OR owner_user_id = :owner)"""
            ),
            {"id": doc_id, "owner": owner_user_id},
        )
    return result.rowcount > 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="python -m server.rag.ingest")
    p.add_argument("path", help="a .md/.pdf file, or a directory of them")
    p.add_argument("--visibility", choices=VISIBILITIES)
    p.add_argument("--lab", dest="lab_id")
    p.add_argument("--owner", dest="owner_user_id")
    p.add_argument("--facility", dest="facility_id")
    p.add_argument("--title")
    p.add_argument("--version")
    args = p.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        print(f"no such path: {target}", file=sys.stderr)
        return 2

    overrides = {
        k: v for k, v in {
            "visibility": args.visibility, "lab_id": args.lab_id,
            "owner_user_id": args.owner_user_id, "facility_id": args.facility_id,
            "title": args.title, "version": args.version,
        }.items() if v
    }

    files = (
        sorted(f for f in target.iterdir() if f.suffix.lower() in (".md", ".markdown", ".pdf"))
        if target.is_dir() else [target]
    )
    if target.is_dir() and args.title:
        print("--title cannot be combined with a directory", file=sys.stderr)
        return 2

    total = 0
    for f in files:
        result = ingest_file(f, **overrides)
        total += result["chunks"]
        print(f"  {result['title']:<44} v{result['version']:<3} "
              f"{result['visibility']:<8} {result['chunks']:>3} chunks")
    print(f"ingested {len(files)} document(s), {total} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
