"""The action branch: build an exact payload, propose it, and stop.

Nothing here mutates Infinity X. The write tools (12–15) produce a pending action; the
graph then interrupts and waits for a human decision delivered through
POST /actions/{id}/approve or /decline.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from server.agent.llm import chat_json
from server.agent.responses import AgentResponse
from server.auth import Ctx
from server.db import session_scope
from server.mcp import tools as tools_mod
from server.mcp.errors import ToolError

log = logging.getLogger("echomind.action")

WRITE_TOOLS = """create_onboarding_request(name, email, lab_id, pi_ack, account_code?)
    Propose onboarding a new user. pi_ack must be true.
create_service_request(template_id, fields)
    Propose a service request. fields must satisfy the template's required fields.
request_booking(instrument_id, starts_at, ends_at, account_code)
    Propose an instrument booking. ISO-8601 UTC timestamps, max 12 hours.
generate_document(template, params)
    template is one of usage_report, onboarding_packet, monthly_summary."""

SYSTEM = """You turn a user's request into exactly one write-tool call for the Infinity X
platform. You never execute anything: the call you describe becomes a pending action that
a human must approve.

Reply only as JSON: {{"tool": "<name>", "arguments": {{...}}}}
If the request does not match any tool, reply {{"tool": null, "why": "<12 words"}}.
If it matches a tool but you are missing something only the user can supply, reply
{{"tool": null, "missing": ["field"], "ask": "<one short question>"}} — never invent the
value and never assume a consent or acknowledgement the user has not actually given.

Tools:
{tools}

Context you must use rather than invent:
- Today is {today} (UTC). Resolve relative dates against it.
- The caller is {user_id} ({role}), labs: {labs}.
- The caller's account codes: {codes}
- Instruments (id — name — status):
{instruments}
- Request templates (id — name — required fields):
{templates}

Rules:
- Resolve pronouns from the conversation: "book it" means the instrument just discussed.
- Use the instrument *id*, never its display name.
- Never guess an account code: use one of the caller's. If they have none, use null.
- Timestamps are ISO-8601 with a Z suffix, e.g. 2026-04-02T09:00:00Z.
- Take the date and time from the conversation. If an earlier turn discussed a specific
  date and window, "book it" means exactly that date and window — do not substitute
  today's date. Fall back to the next occurrence after today only when no date appears
  anywhere in the conversation.
- pi_ack is true ONLY if the user has actually said the PI acknowledges it. Being a PI
  is not the same as having said so. If they have not, ask.
- Enum fields must use one of the listed options exactly as written ("150bp", not "150").
- Take field values from the user's own words or from their documents. Never invent one."""


def _template_line(t) -> str:
    """One template with its required fields and, for enums, the exact allowed values."""
    parts = []
    for field in t["fields"]:
        if not field.get("required"):
            continue
        if field.get("type") == "enum":
            parts.append(f"{field['name']} (one of: {'|'.join(field.get('options', []))})")
        else:
            parts.append(f"{field['name']} ({field.get('type', 'string')})")
    return f"    {t['id']} — {t['name']} — requires: " + ", ".join(parts)


def _catalog(ctx: Ctx) -> dict[str, str]:
    with session_scope() as s:
        instruments = s.execute(
            text("SELECT id, name, status FROM infinity.instruments ORDER BY name")
        ).mappings().all()
        templates = s.execute(
            text("SELECT id, name, fields FROM infinity.request_templates ORDER BY name")
        ).mappings().all()
        codes = s.execute(
            text("SELECT account_codes FROM infinity.users WHERE id = :id"),
            {"id": ctx.user_id},
        ).scalar_one_or_none()

    return {
        "instruments": "\n".join(
            f"    {i['id']} — {i['name']} — {i['status']}" for i in instruments
        ),
        "templates": "\n".join(_template_line(t) for t in templates),
        "codes": ", ".join(codes or []) or "none",
    }


def _document_context(message: str, ctx: Ctx) -> str:
    """Documents the caller can see that bear on this request.

    Scene 4 of the demo submits a filled form the user uploaded: the planner has to read
    the form to fill the template's fields. Retrieval is the permission-filtered path, so
    this cannot surface a document the caller is not entitled to.
    """
    try:
        from server.rag.retrieval import retrieve

        chunks = retrieve(message, ctx, k=3)
    except Exception as exc:
        log.warning("could not load document context: %s", exc)
        return ""
    if not chunks:
        return ""
    body = "\n\n".join(f"[{c.breadcrumb}]\n{c.text}" for c in chunks)
    return (
        "\n\nDOCUMENTS THE CALLER CAN SEE (use these to fill in field values the user "
        f"has already written down; do not invent values):\n{body}"
    )


def plan(message: str, ctx: Ctx, history: str = "") -> dict[str, Any]:
    catalog = _catalog(ctx)
    system = SYSTEM.format(
        tools=WRITE_TOOLS,
        today=datetime.now(UTC).date().isoformat(),
        user_id=ctx.user_id,
        role=ctx.role,
        labs=", ".join(ctx.lab_ids) or "none",
        **catalog,
    )
    user = message
    if history:
        user = f"{history}\n\nCURRENT REQUEST: {message}"
    user += _document_context(message, ctx)

    return chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        default={"tool": None, "why": "planner unavailable"},
        max_tokens=400,
    )


def propose(
    message: str, ctx: Ctx, thread_id: str | None = None, history: str = ""
) -> AgentResponse:
    """Create the pending action and return the approval request."""
    chosen = plan(message, ctx, history)
    name = chosen.get("tool")

    if name not in tools_mod.WRITE_TOOLS:
        # Missing information the user alone can supply: ask, rather than inventing it
        # or firing a call that the tool will only reject.
        question = str(chosen.get("ask") or "").strip()
        if question:
            return AgentResponse(
                response_type="redirect",
                route="action",
                text=question,
                meta={"plan": chosen, "awaiting": chosen.get("missing")},
            )
        return AgentResponse(
            response_type="redirect",
            route="action",
            text=(
                "I can propose bookings, service requests, onboarding requests and "
                "facility documents. I could not map that request onto any of them — "
                "could you say which one you meant?"
            ),
            meta={"plan": chosen},
        )

    arguments = chosen.get("arguments") or {}
    try:
        pending = tools_mod.call(ctx, name, arguments)
    except ToolError as exc:
        return AgentResponse(
            response_type="redirect",
            route="action",
            text=(
                f"I could not prepare that request. {exc.message} {exc.hint}".strip()
            ),
            meta={"error": exc.to_dict()["error"], "plan": chosen},
        )

    if thread_id:
        with session_scope() as s:
            s.execute(
                text("UPDATE echomind.actions SET thread_id = :t WHERE id = :id"),
                {"t": thread_id, "id": pending["action_id"]},
            )

    return AgentResponse(
        response_type="approval_request",
        route="action",
        text=(
            f"I've prepared this, but I haven't done it. {pending['payload_preview']}.\n\n"
            "Approve it and I'll execute it; decline and nothing happens. "
            "Either way it goes in the audit log."
        ),
        pending_action=pending,
        meta={"plan": chosen},
    )


def confirmation_text(action: dict[str, Any]) -> str:
    """Post-decision message. Every value comes from the action's stored result row."""
    status = action.get("status")
    result = action.get("result") or {}

    if status == "declined":
        return "Declined — nothing was changed. The decision is recorded in the audit log."

    if status == "failed":
        return (
            "The approval went through but the change did not: "
            f"{result.get('error', 'the platform rejected it')}. "
            "Nothing was left half-done, and the failure is in the audit log."
        )

    created = result.get("created")
    if created == "booking":
        # Report the status the row actually has — the facility still confirms the slot.
        return (
            f"Done. Booking {result['booking_id']} is now '{result['status']}' on "
            f"{result['instrument_id']} from {result['starts_at']} to {result['ends_at']}."
        )
    if created == "service_request":
        return (
            f"Submitted. Request {result['request_id']} is now "
            f"'{result['status']}' against template {result['template_id']}."
        )
    if created == "user":
        return (
            f"Onboarded. {result['email']} now exists as {result['user_id']}. "
            "They will need instrument training before they can book."
        )
    if created == "document":
        return (
            f"Generated {result['title']} — saved to {result['path']} "
            f"({result['bytes']} bytes)."
        )
    return f"Done. The action completed with status '{status}'."
