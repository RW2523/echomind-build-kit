"""Regenerate the tool half of docs/module-map.md from the live registry.

    python -m scripts.module_map

A hand-written inventory of tools is wrong the moment someone adds one. This reads
`server.mcp.tools.TOOLS`, so the document can only describe tools that actually exist.
"""

from __future__ import annotations

from server.config import REPO_ROOT
from server.mcp.tools import TOOLS

DOC = REPO_ROOT / "docs" / "module-map.md"
MARKER = "<!-- generated: scripts/module_map.py -->"

# Which Infinity X module each tool serves. The only hand-maintained part, and --check
# fails if a tool is added without one, so it cannot silently fall out of date.
MODULES = {
    # tool name: (Infinity X module, the question a user actually asks)
    "get_user_profile": ("Identity & training records",
                         "Who am I, which lab, what am I trained on, which account codes"),
    "get_facility_catalog": ("Instrument catalogue & rates",
                             "What instruments exist, in which core, at what hourly rate"),
    "check_availability": ("Scheduling — availability",
                           "When is this instrument free between two dates"),
    "get_my_bookings": ("Scheduling — bookings",
                        "What have I booked, and what is its status"),
    "get_usage_records": ("Usage analytics",
                          "Scheduled versus tracked hours, by user, lab or instrument"),
    "get_request_status": ("Service requests",
                           "Where has my request got to, and what happened to it"),
    "track_sample": ("Sample tracking",
                     "Where is this barcode now, and what has been done to it"),
    "get_billing_summary": ("Billing & invoicing",
                            "What was this account charged this period, line by line"),
    "get_project_overview": ("Projects",
                             "Who is on this project, which cores it uses, what it has spent"),
    "get_instrument_health": ("Instrument health",
                              "Is it up, when was it last serviced, how much downtime"),
    "run_readonly_sql": ("Reporting",
                         "Anything else, in natural language, over four allow-listed views"),
    "request_booking": ("Scheduling — booking (write)",
                        "Book this instrument for me — proposed, never executed unasked"),
    "create_service_request": ("Service requests (write)",
                               "Raise this request from a template or an uploaded form"),
    "create_onboarding_request": ("Onboarding (write)",
                                  "Get this person an account, with the PI's acknowledgement"),
    "generate_document": ("Reporting documents (write)",
                          "Produce a usage report, onboarding packet or monthly summary"),
}


def table() -> str:
    lines = [
        "",
        "| # | Tool | Infinity X module | Tier | What it answers |",
        "|---:|---|---|---|---|",
    ]
    for name, spec in sorted(TOOLS.items(), key=lambda kv: kv[1].number):
        module, answers = MODULES.get(name, ("**UNMAPPED**", ""))
        lines.append(f"| {spec.number} | `{name}` | {module} | {spec.tier} | {answers} |")
    lines += ["", f"{len(TOOLS)} tools registered.", ""]
    return "\n".join(lines)


def main() -> int:
    unmapped = [n for n in TOOLS if n not in MODULES]
    if unmapped:
        print(f"tools with no module mapping: {', '.join(unmapped)}")
        return 1
    stale = [n for n in MODULES if n not in TOOLS]
    if stale:
        print(f"mapping names a tool that no longer exists: {', '.join(stale)}")
        return 1

    text = DOC.read_text()
    head = text.split(MARKER)[0] + MARKER
    DOC.write_text(head + "\n" + table())
    print(f"wrote {DOC.relative_to(REPO_ROOT)} — {len(TOOLS)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
