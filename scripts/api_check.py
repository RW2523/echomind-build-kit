"""Every endpoint, four ways: valid, malformed, unauthenticated, and wrongly entitled.

A 200 proves a route is wired. It does not prove the route refuses what it should, and
the refusals are the half this product is built on — so every case names the status it
expects and anything else is a finding.

Two expectations look wrong and are not. /admin/* and /dataspaces answer a non-admin with
404 rather than 403, on purpose and in the code's own words: "an admin surface should not
confirm its own existence." A 403 there would tell a caller exactly what they had found.
The same rule gives 404 for another user's private upload.

    python -m scripts.api_check [findings.json]
"""
import json, sys
import httpx

BASE = "http://localhost:8080"
c = httpx.Client(timeout=180.0)
findings, checked = [], 0


def tok(handle):
    return c.post(f"{BASE}/demo/login/{handle}").json()["token"]


TOKENS = {h: tok(h) for h in ("alice", "bob", "asha", "cora")}
H = {h: {"Authorization": f"Bearer {t}"} for h, t in TOKENS.items()}
BAD = {"Authorization": "Bearer not.a.real.jwt"}


def hit(label, verb, path, *, headers=None, expect=200, json_body=None, note=""):
    global checked
    checked += 1
    try:
        r = c.request(verb, f"{BASE}{path}", headers=headers or {}, json=json_body)
    except Exception as exc:
        findings.append(f"[{label}] {verb} {path} RAISED {exc}")
        print(f"  ERR  {verb:6} {path[:52]:54} {exc}")
        return None
    ok = r.status_code in (expect if isinstance(expect, (list, tuple)) else [expect])
    mark = "ok " if ok else "BAD"
    print(f"  {mark}  {verb:6} {path[:52]:54} {r.status_code} {note}")
    if not ok:
        findings.append(
            f"[{label}] {verb} {path} -> {r.status_code}, expected {expect}. "
            f"{r.text[:120]}"
        )
    return r


print("=== 1. unauthenticated: every protected route must refuse ===")
for verb, path in [
    ("GET", "/tools"), ("GET", "/actions"), ("GET", "/library"), ("GET", "/conversations"),
    ("GET", "/me/memory"), ("GET", "/uploads"), ("GET", "/dataspaces"),
    ("GET", "/admin/summary"), ("GET", "/admin/audit"), ("GET", "/admin/traces"),
]:
    hit("noauth", verb, path, expect=401)
hit("noauth", "POST", "/chat", json_body={"message": "hi"}, expect=401)

print("\n=== 2. invalid token ===")
for verb, path in [("GET", "/tools"), ("GET", "/actions"), ("GET", "/library")]:
    hit("badtoken", verb, path, headers=BAD, expect=401)

print("\n=== 3. public/health ===")
hit("open", "GET", "/healthz")
hit("open", "GET", "/readyz")
hit("open", "GET", "/demo/users")
hit("open", "POST", "/demo/login/alice")
hit("open", "POST", "/demo/login/nobody", expect=(400, 404))

print("\n=== 4. authenticated reads (alice) ===")
for path in ["/tools", "/actions", "/library", "/conversations", "/me/memory", "/uploads"]:
    hit("read", "GET", path, headers=H["alice"])
# The console is an admin surface and answers a plain user with 404 by design — "an admin
# surface should not confirm its own existence", same rule as /admin below.
for path in ["/dataspaces", "/dataspaces/tools", "/dataspaces/pipeline"]:
    hit("read", "GET", path, headers=H["cora"])
    hit("read", "GET", path, headers=H["alice"], expect=404, note="(hidden from a user)")

print("\n=== 5. admin-only surface ===")
for path in ["/admin/summary", "/admin/audit", "/admin/gaps", "/admin/traces",
             "/admin/prompts", "/admin/evals"]:
    hit("admin-ok", "GET", path, headers=H["cora"])
    # 404 rather than 403 on purpose: the surface does not confirm it exists.
    hit("admin-denied", "GET", path, headers=H["alice"], expect=404,
        note="(hidden from a user)")

print("\n=== 6. malformed input ===")
hit("bad", "POST", "/chat", headers=H["alice"], json_body={}, expect=422, note="no message")
hit("bad", "POST", "/chat", headers=H["alice"], json_body={"message": ""}, expect=422,
    note="empty message")
hit("bad", "POST", "/chat", headers=H["alice"], json_body={"message": "x" * 5000},
    expect=422, note="over 4000 chars")
hit("bad", "POST", "/chat", headers=H["alice"],
    json_body={"message": "hi", "thread_id": "../../etc/passwd"}, expect=(200, 422),
    note="path-ish thread id")
hit("bad", "GET", "/library/does-not-exist", headers=H["alice"], expect=404)
hit("bad", "GET", "/actions/act-nope", headers=H["alice"], expect=404)
hit("bad", "POST", "/actions/act-nope/approve", headers=H["alice"], expect=(404, 400))
hit("bad", "GET", "/threads/thr-nope", headers=H["alice"], expect=(200, 404))
hit("bad", "GET", "/tools/chunk/999999", headers=H["alice"], expect=404)
hit("bad", "POST", "/tools/no_such_tool", headers=H["alice"], json_body={}, expect=(400, 404))
hit("bad", "GET", "/dataspaces/rows/pg_catalog/pg_user", headers=H["cora"], expect=(400, 403, 404),
    note="not allow-listed")
hit("bad", "GET", "/dataspaces/rows/reporting/v_billing_lines?limit=99999",
    headers=H["cora"], expect=422, note="over the row cap")

print("\n=== 7. tool endpoint, directly ===")
hit("tool", "POST", "/tools/get_my_bookings", headers=H["alice"], json_body={})
hit("tool", "POST", "/tools/get_my_bookings", headers=H["alice"],
    json_body={"date_from": "not-a-date"}, expect=(400, 422), note="bad date")
hit("tool", "POST", "/tools/get_billing_summary", headers=H["alice"],
    json_body={"account_code": "ACC-B1", "period": "2026-03"}, expect=(403, 400),
    note="another lab's code")
hit("tool", "POST", "/tools/get_instrument_health", headers=H["alice"],
    json_body={"instrument_id": "ins-confocal-c2"}, expect=200)
hit("tool", "POST", "/tools/run_readonly_sql", headers=H["alice"],
    json_body={"sql": "SELECT 1"}, expect=(403, 400), note="alice has no SQL tier")
hit("tool", "POST", "/tools/run_readonly_sql", headers=H["cora"],
    json_body={"sql": "DROP TABLE infinity.bookings"}, expect=(400, 403), note="not a SELECT")
hit("tool", "POST", "/tools/run_readonly_sql", headers=H["cora"],
    json_body={"sql": "SELECT * FROM infinity.users"}, expect=(400, 403),
    note="not an allow-listed view")
hit("tool", "POST", "/tools/run_readonly_sql", headers=H["cora"],
    json_body={"sql": "SELECT count(*) FROM v_bookings"}, expect=200, note="permitted")

print("\n=== 8. cross-user isolation ===")
hit("iso", "POST", "/tools/track_sample", headers=H["bob"],
    json_body={"barcode": "BC100000"}, expect=(200, 403), note="bob vs a lab-a sample")
r = c.get(f"{BASE}/actions", headers=H["alice"]).json()
acts = r.get("actions") or []
if acts:
    aid = acts[0].get("action_id") or acts[0]["id"]
    hit("iso", "GET", f"/actions/{aid}", headers=H["bob"], expect=(403, 404),
        note="bob reading alice's action")
    hit("iso", "POST", f"/actions/{aid}/approve", headers=H["bob"], expect=(403, 404, 400),
        note="bob approving alice's action")

print("\n=== 9. uploads ===")
files = {"file": ("probe.md", b"# probe\nnothing here\n", "text/markdown")}
r = c.post(f"{BASE}/uploads", headers=H["alice"], files=files)
print(f"  {'ok ' if r.status_code == 200 else 'BAD'}  POST   /uploads{'':46} {r.status_code}")
checked += 1
doc_id = (r.json() or {}).get("doc_id") if r.status_code == 200 else None
if r.status_code != 200:
    findings.append(f"[upload] POST /uploads -> {r.status_code} {r.text[:120]}")
hit("upload", "POST", "/uploads", headers=H["alice"], expect=422, note="no file")
r2 = c.post(f"{BASE}/uploads", headers=H["alice"],
            files={"file": ("x.exe", b"MZ", "application/octet-stream")})
print(f"  {'ok ' if r2.status_code == 400 else 'BAD'}  POST   /uploads (bad type){'':35} {r2.status_code}")
checked += 1
if r2.status_code != 400:
    findings.append(f"[upload] .exe accepted -> {r2.status_code}")
if doc_id:
    hit("upload", "GET", f"/library/{doc_id}", headers=H["alice"])
    hit("upload", "GET", f"/library/{doc_id}", headers=H["bob"], expect=(403, 404),
        note="bob reading alice's private upload")
    hit("upload", "DELETE", f"/uploads/{doc_id}", headers=H["bob"], expect=(403, 404),
        note="bob deleting alice's upload")
    hit("upload", "DELETE", f"/uploads/{doc_id}", headers=H["alice"])

print("\n=== 10. chat control ===")
hit("chat", "POST", "/chat/reset", headers=H["alice"], json_body={}, expect=(200, 422))

print("\n" + "=" * 64)
print(f"{checked} checks · {len(findings)} finding(s)")
for f in findings:
    print(f"  - {f}")
if len(sys.argv) > 1:
    json.dump(findings, open(sys.argv[1], "w"), indent=1)
sys.exit(1 if findings else 0)
