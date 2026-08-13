"""The shelf must show exactly what the retriever would read — no more, no less.

A listing that is more permissive than retrieval is a directory of things the reader is
not allowed to have, which is worse than no listing at all: it tells them a document
exists, who it belongs to, and what it is called. A listing that is less permissive is
merely useless. So these tests pin the two lists to each other rather than to a fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.db import session_scope
from server.rag import retrieval

pytestmark = pytest.mark.rag_isolation


@pytest.fixture(scope="module")
def client():
    from server.main import app

    with TestClient(app) as c:
        yield c


def _get(client, token, path):
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


def _listing(client, token) -> list[dict]:
    r = _get(client, token, "/library")
    assert r.status_code == 200
    return r.json()["documents"]


# --- the listing is the retrieval filter, not a second copy of the rule ---------------


@pytest.mark.parametrize("who", ["alice", "bob", "asha", "cora"])
def test_the_shelf_matches_what_that_user_could_actually_retrieve(who, client, tokens, ctxs):
    """The one property worth having. Anything the listing shows must be reachable by the
    retriever for the same caller, and anything reachable must be listed."""
    permitted, params = retrieval.permission_predicate(ctxs[who])
    with session_scope() as s:
        retrievable = {
            row[0]
            for row in s.execute(
                text(f"SELECT DISTINCT c.doc_id FROM echomind.chunks c WHERE {permitted}"),
                params,
            )
        }
    listed = {d["doc_id"] for d in _listing(client, tokens[who])}
    assert listed == retrievable


def test_nobody_elses_private_note_is_listed(client, tokens):
    private = "doc-alice-private-experiment-notes-v1-0"
    assert private in {d["doc_id"] for d in _listing(client, tokens["alice"])}
    for other in ("bob", "asha", "cora"):
        assert private not in {d["doc_id"] for d in _listing(client, tokens[other])}


def test_an_admin_sees_every_lab_but_still_not_a_private_note(client, tokens):
    """Admins additionally see facility-scoped documents — never another user's private
    chunks. An admin who can read a personal note is a different product."""
    listed = _listing(client, tokens["cora"])
    assert {d["doc_id"] for d in listed if d["visibility"] == "private"} == set()
    assert len({d["scope"] for d in listed if d["visibility"] == "lab"}) > 1


def test_a_user_does_not_see_another_labs_protocols(client, tokens):
    alice_labs = {d["scope"] for d in _listing(client, tokens["alice"]) if d["visibility"] == "lab"}
    bob_labs = {d["scope"] for d in _listing(client, tokens["bob"]) if d["visibility"] == "lab"}
    assert alice_labs and bob_labs
    assert not (alice_labs & bob_labs)


def test_the_shelf_needs_a_caller(client):
    assert client.get("/library").status_code in (401, 403)


def test_every_listed_document_carries_what_a_reader_needs_to_choose(client, tokens):
    for doc in _listing(client, tokens["asha"]):
        assert doc["title"]
        assert doc["chunks"] >= 1
        assert doc["scope"]
        assert doc["visibility"] in ("public", "lab", "private")


# --- the preview ---------------------------------------------------------------------


def test_a_public_policy_previews_in_full(client, tokens):
    doc_id = "doc-recharge-billing-and-invoice-approval-policy-v3-3"
    body = _get(client, tokens["asha"], f"/library/{doc_id}").json()
    assert body["title"].startswith("Recharge, Billing")
    assert body["scope"] == "everyone"
    assert len(body["sections"]) >= 1
    assert all(s["text"].strip() for s in body["sections"])
    assert [s["ord"] for s in body["sections"]] == sorted(s["ord"] for s in body["sections"])


def test_the_preview_shows_the_indexed_chunks_not_the_file_on_disk(client, tokens):
    """The file and the index can drift, and the preview exists precisely so a reader can
    check what an answer was drawn from."""
    doc_id = "doc-recharge-billing-and-invoice-approval-policy-v3-3"
    body = _get(client, tokens["asha"], f"/library/{doc_id}").json()
    with session_scope() as s:
        indexed = [
            row[0]
            for row in s.execute(
                text("SELECT text FROM echomind.chunks WHERE doc_id = :d ORDER BY ord"),
                {"d": doc_id},
            )
        ]
    assert [s["text"] for s in body["sections"]] == indexed


@pytest.mark.parametrize(
    "doc_id, allowed, refused",
    [
        ("doc-alice-private-experiment-notes-v1-0", ["alice"], ["bob", "asha", "cora"]),
        ("doc-ferreira-lab-sample-fixation-protocol-1-v1-0", ["bob", "cora"], ["alice", "asha"]),
    ],
)
def test_previewing_is_bounded_by_the_same_rule_as_listing(
    doc_id, allowed, refused, client, tokens
):
    for who in allowed:
        assert _get(client, tokens[who], f"/library/{doc_id}").status_code == 200
    for who in refused:
        # Not 403: a caller who may not read it cannot learn that it exists.
        assert _get(client, tokens[who], f"/library/{doc_id}").status_code == 404


def test_a_document_that_does_not_exist_looks_the_same_as_one_you_may_not_read(client, tokens):
    assert _get(client, tokens["alice"], "/library/doc-no-such-thing").status_code == 404


def test_a_doc_id_cannot_be_used_to_reach_past_the_filter(client, tokens):
    for hostile in ("../../etc/passwd", "doc-x' OR '1'='1", "%2e%2e%2f"):
        assert _get(client, tokens["alice"], f"/library/{hostile}").status_code in (404, 400)


def test_the_totals_agree_with_the_rows(client, tokens):
    body = _get(client, tokens["asha"], "/library").json()
    assert body["total"] == len(body["documents"])
    assert body["chunks"] == sum(d["chunks"] for d in body["documents"])


# --- the seam between the two servers -------------------------------------------------


def test_every_api_route_is_proxied_by_the_dev_server():
    """A route the proxy does not forward is answered by the SPA fallback with index.html,
    so the browser gets 200 text/html and JSON.parse fails on "<!doctype". The reader is
    then sent looking at the API for a fault that is in vite.config.ts — which is exactly
    what happened when /library shipped without its proxy entry.
    """
    import json as _json
    import re

    from server.config import REPO_ROOT
    from server.main import app

    config = (REPO_ROOT / "ui" / "vite.config.ts").read_text(encoding="utf-8")
    block = re.search(r"proxy:\s*\{(.*?)\n\s*\},", config, re.S)
    assert block, "could not find the proxy block in ui/vite.config.ts"
    proxied = set(re.findall(r'"(/[^"]*)"\s*:', block.group(1)))

    # From the OpenAPI schema, not app.routes: this FastAPI wraps each included router in
    # an object whose .path is None, so walking app.routes finds only /healthz and the doc
    # endpoints — a version of this test that did exactly that passed while /library was
    # still unproxied, which is worse than not having written it.
    prefixes = {"/" + path.lstrip("/").split("/")[0] for path in app.openapi()["paths"]}
    # /healthz and /readyz are proxied anyway; /mcp is the MCP mount, spoken to by MCP
    # clients rather than by this UI, and is deliberately absent from the schema.
    missing = sorted(prefixes - proxied)
    assert not missing, (
        f"these API prefixes are not in ui/vite.config.ts proxy: {missing}. "
        f"Requests to them will be answered with index.html instead. "
        f"({_json.dumps(sorted(proxied))} are)"
    )
