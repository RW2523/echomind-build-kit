"""The LangGraph agent.

    START -> route -> knowledge | data | action | smalltalk | out_of_scope -> END

The action branch interrupts after proposing a pending action and resumes when a human
decides, using the Postgres checkpointer keyed by thread_id (= conversation id).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from server.agent import action as action_branch
from server.agent import data as data_branch
from server.agent import knowledge as knowledge_branch
from server.agent import router as router_mod
from server.agent.responses import AgentResponse, scope_response
from server.auth import Ctx
from server.config import settings
from server.observability import tracer

log = logging.getLogger("echomind.graph")

# Must match scripts/seed.py — that is where these tables are created.
CHECKPOINT_SCHEMA = "echomind"

SMALLTALK = (
    "I'm EchoMind, the assistant for this core facility. I can answer questions about "
    "facility policies and SOPs, look up your bookings, usage and invoices, track "
    "samples and requests, and prepare bookings or documents for your approval. "
    "Everything I tell you comes from the platform's own records — if I can't verify "
    "something, I'll say so."
)


HISTORY_TURNS = 4  # how much of the conversation the planners get to see


class AgentState(TypedDict, total=False):
    message: str
    ctx: dict[str, Any]          # serialized Ctx; rebuilt in each node
    thread_id: str
    route: str
    route_why: str
    response: dict[str, Any]     # AgentResponse.to_dict()
    pending_action_id: str | None
    history: list[dict[str, str]]


def _extend_history(state: AgentState, response: AgentResponse) -> list[dict[str, str]]:
    """Keep a short rolling transcript so follow-ups like "book it" resolve.

    Trimmed hard: the planners need enough context to resolve a pronoun, not the whole
    conversation, and a long transcript is both slower and easier for a small model to
    get lost in.
    """
    previous = list(state.get("history") or [])
    previous.append(
        {
            "q": state.get("message", ""),
            "a": (response.text or "")[:400],
            "type": response.response_type,
        }
    )
    return previous[-HISTORY_TURNS:]


def format_history(history: list[dict[str, str]] | None) -> str:
    if not history:
        return ""
    lines = ["EARLIER IN THIS CONVERSATION (most recent last):"]
    for turn in history:
        lines.append(f"  user: {turn.get('q', '')}")
        lines.append(f"  assistant ({turn.get('type', '')}): {turn.get('a', '')}")
    return "\n".join(lines)


def ctx_to_dict(ctx: Ctx) -> dict[str, Any]:
    return {
        "user_id": ctx.user_id,
        "name": ctx.name,
        "role": ctx.role,
        "lab_ids": list(ctx.lab_ids),
        "facility_ids": list(ctx.facility_ids),
    }


def ctx_from_dict(data: dict[str, Any]) -> Ctx:
    return Ctx(
        user_id=data["user_id"],
        name=data.get("name", data["user_id"]),
        role=data.get("role", "user"),
        lab_ids=tuple(data.get("lab_ids") or ()),
        facility_ids=tuple(data.get("facility_ids") or ()),
    )


# --- nodes -------------------------------------------------------------------------


def node_route(state: AgentState) -> dict[str, Any]:
    with tracer.span("node.route") as span:
        chosen, why = router_mod.route(
            state["message"], history=format_history(state.get("history"))
        )
        span.set(route=chosen, why=why)
    return {"route": chosen, "route_why": why}


def node_knowledge(state: AgentState) -> dict[str, Any]:
    with tracer.span("node.knowledge") as span:
        response = knowledge_branch.answer(state["message"], ctx_from_dict(state["ctx"]))
        response.route = "knowledge"
        span.set(
            route="knowledge",
            response_type=response.response_type,
            gate_result=(response.gate or {}).get("reason"),
            citations=len(response.citations),
            faithfulness=(response.faithfulness or {}).get("score"),
        )
    return {"response": response.to_dict(), "history": _extend_history(state, response)}


def node_data(state: AgentState) -> dict[str, Any]:
    with tracer.span("node.data") as span:
        response = data_branch.answer(
            state["message"], ctx_from_dict(state["ctx"]),
            history=format_history(state.get("history")),
        )
        response.route = "data"
        span.set(
            route="data",
            response_type=response.response_type,
            sql_valid=response.executed_sql is not None,
            row_count=len(response.rows),
        )
    return {"response": response.to_dict(), "history": _extend_history(state, response)}


def node_smalltalk(state: AgentState) -> dict[str, Any]:
    with tracer.span("node.smalltalk") as span:
        span.set(route="smalltalk", response_type="smalltalk")
    response = AgentResponse(response_type="smalltalk", text=SMALLTALK, route="smalltalk")
    return {"response": response.to_dict(), "history": _extend_history(state, response)}


def node_out_of_scope(state: AgentState) -> dict[str, Any]:
    with tracer.span("node.out_of_scope") as span:
        span.set(route="out_of_scope", response_type="scope")
    response = scope_response()
    return {"response": response.to_dict(), "history": _extend_history(state, response)}


def node_action_propose(state: AgentState) -> dict[str, Any]:
    """Build the payload and create the pending action. Never interrupts."""
    ctx = ctx_from_dict(state["ctx"])
    with tracer.span("node.action_propose") as span:
        proposal = action_branch.propose(
            state["message"], ctx, thread_id=state.get("thread_id"),
            history=format_history(state.get("history")),
        )
        span.set(
            route="action",
            response_type=proposal.response_type,
            action_kind=(proposal.pending_action or {}).get("kind"),
        )
    return {
        "response": proposal.to_dict(),
        "history": _extend_history(state, proposal),
        "pending_action_id": (
            proposal.pending_action["action_id"]
            if proposal.response_type == "approval_request"
            else None
        ),
    }


def node_action_wait(state: AgentState) -> dict[str, Any]:
    """Stop and wait for a human decision.

    Deliberately a separate node containing nothing before `interrupt()`. LangGraph
    re-executes an interrupted node from the top when it resumes, so any work placed
    above the interrupt would run a second time — which, with a write tool above it,
    meant proposing the action twice and failing the second attempt on its own booking.
    """
    proposal = state["response"]
    decision = interrupt(
        {
            "kind": "approval_request",
            "action_id": state.get("pending_action_id"),
            "preview": (proposal.get("pending_action") or {}).get("payload_preview"),
            "response": proposal,
        }
    )

    # --- resumed: the decided action row, straight from the database ---
    decided = decision if isinstance(decision, dict) else {}
    return {
        "response": AgentResponse(
            response_type="answer" if decided.get("status") == "executed" else "redirect",
            text=action_branch.confirmation_text(decided),
            route="action",
            pending_action=decided,
        ).to_dict()
    }


def _after_propose(state: AgentState) -> str:
    """Only wait for approval if something is actually pending."""
    return "action_wait" if state.get("pending_action_id") else END


def _branch(state: AgentState) -> str:
    return {
        "knowledge": "knowledge",
        "data": "data",
        "action": "action_propose",
        "smalltalk": "smalltalk",
        "out_of_scope": "out_of_scope",
    }.get(state.get("route", "knowledge"), "knowledge")


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    builder.add_node("route", node_route)
    builder.add_node("knowledge", node_knowledge)
    builder.add_node("data", node_data)
    builder.add_node("action_propose", node_action_propose)
    builder.add_node("action_wait", node_action_wait)
    builder.add_node("smalltalk", node_smalltalk)
    builder.add_node("out_of_scope", node_out_of_scope)

    builder.add_edge(START, "route")
    builder.add_conditional_edges("route", _branch, {
        "knowledge": "knowledge",
        "data": "data",
        "action_propose": "action_propose",
        "smalltalk": "smalltalk",
        "out_of_scope": "out_of_scope",
    })
    builder.add_conditional_edges("action_propose", _after_propose, {
        "action_wait": "action_wait",
        END: END,
    })
    for node in ("knowledge", "data", "action_wait", "smalltalk", "out_of_scope"):
        builder.add_edge(node, END)

    return builder.compile(checkpointer=checkpointer)


# --- singleton wiring ---------------------------------------------------------------

_checkpointer: PostgresSaver | None = None
_graph = None
_cm = None


def _checkpointer_conn_string() -> str:
    """Plain libpq URL for PostgresSaver, with the checkpoint schema pinned.

    The saver uses unqualified table names. Left to the default search_path they resolve
    against a schema named after the connecting role, so the owner and the app would look
    in two different places — the app in one that does not exist.
    """
    url = settings.runtime_database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-csearch_path%3Decho_schema".replace(
        "echo_schema", CHECKPOINT_SCHEMA
    )


def get_graph():
    """Compiled graph with the Postgres checkpointer, created once per process."""
    global _graph, _checkpointer, _cm
    if _graph is None:
        _cm = PostgresSaver.from_conn_string(_checkpointer_conn_string())
        _checkpointer = _cm.__enter__()
        try:
            _checkpointer.setup()
        except Exception as exc:
            # setup() is DDL. When the API runs as echomind_app it has none, by design —
            # the seeder created these tables as owner. Only a genuinely missing table
            # is a problem, and that surfaces on the first turn with a clear error.
            log.info("checkpointer setup skipped (%s: %s)", type(exc).__name__, exc)
        _graph = build_graph(_checkpointer)
        log.info(
            "agent graph compiled with Postgres checkpointer (role=%s)",
            "owner" if settings.runs_as_owner else "echomind_app",
        )
    return _graph


def run_turn(message: str, ctx: Ctx, thread_id: str) -> AgentResponse:
    """One chat turn. Returns the approval request itself if the graph interrupts.

    The root trace is opened here rather than in the HTTP layer so that every entry point
    — chat endpoint, eval runner, demo script — produces one trace per turn with the node
    and tool spans nested under it (spec 06).
    """
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    with tracer.trace("chat.turn", user_id=ctx.user_id, thread_id=thread_id) as span:
        span.set(question=message[:500], role=ctx.role)
        result = graph.invoke(
            {
                "message": message,
                "ctx": ctx_to_dict(ctx),
                "thread_id": thread_id,
            },
            config=config,
        )

        interrupts = result.get("__interrupt__")
        if interrupts:
            response = AgentResponse(**_response_kwargs(interrupts[0].value["response"]))
        else:
            response = AgentResponse(**_response_kwargs(result["response"]))

        span.set(
            route=response.route,
            response_type=response.response_type,
            gate_result=(response.gate or {}).get("reason"),
            sql_valid=response.executed_sql is not None,
            action_kind=(response.pending_action or {}).get("kind"),
            escalated=False,
            citations=len(response.citations),
        )
    return response


def resume_turn(thread_id: str, decision: dict[str, Any]) -> AgentResponse | None:
    """Resume an interrupted thread after approve/decline. None if nothing was waiting."""
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = graph.get_state(config)
    if not snapshot.next:
        log.info("thread %s has nothing to resume", thread_id)
        return None

    result = graph.invoke(Command(resume=decision), config=config)
    return AgentResponse(**_response_kwargs(result["response"]))


def _response_kwargs(data: dict[str, Any]) -> dict[str, Any]:
    """Rebuild an AgentResponse from its serialized form (citations become dicts)."""
    from server.agent.responses import Citation

    payload = dict(data)
    payload["citations"] = [
        Citation(**c) if isinstance(c, dict) else c for c in payload.get("citations", [])
    ]
    return payload
