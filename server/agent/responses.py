"""The response envelope every branch of the graph produces.

`response_type` is the contract with the UI (spec 04 state):
    answer            grounded, cited knowledge answer
    rows_answer       data answer whose values came from SQL rows
    approval_request  a pending action awaiting human approval
    redirect          the honest "I could not verify this" reply
    scope             out-of-scope refusal
    smalltalk         brief friendly reply
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RESPONSE_TYPES = (
    "answer",
    "rows_answer",
    "approval_request",
    "redirect",
    "scope",
    "smalltalk",
)

SCOPE_MESSAGE = (
    "I only cover the Infinity X core-facility platform. I can help with facility "
    "policies and SOPs, instrument availability and bookings, training requirements, "
    "service requests and sample tracking, usage and billing records, projects, and "
    "generating facility documents. Ask me something in that world and I'll answer it "
    "from verified records."
)


@dataclass
class Citation:
    index: int
    doc_id: str
    breadcrumb: str
    title: str
    chunk_id: int
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "doc_id": self.doc_id,
            "breadcrumb": self.breadcrumb,
            "title": self.title,
            "chunk_id": self.chunk_id,
            "score": round(self.score, 4),
        }


@dataclass
class AgentResponse:
    response_type: str
    text: str
    citations: list[Citation] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    executed_sql: str | None = None
    pending_action: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    faithfulness: dict[str, Any] | None = None
    route: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_type": self.response_type,
            "text": self.text,
            "citations": [c.to_dict() for c in self.citations],
            "rows": self.rows,
            "columns": self.columns,
            "executed_sql": self.executed_sql,
            "pending_action": self.pending_action,
            "gate": self.gate,
            "faithfulness": self.faithfulness,
            "route": self.route,
            "meta": self.meta,
        }


def scope_response() -> AgentResponse:
    return AgentResponse(response_type="scope", text=SCOPE_MESSAGE, route="out_of_scope")
