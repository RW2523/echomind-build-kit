"""Escalation stub — present, wired, and off.

Golden rule 6: no cloud LLM calls anywhere in the core path. This module exists so the
escape hatch is designed rather than improvised later, but ESCALATION_ENABLED defaults to
false and `should_escalate()` then returns False unconditionally. A unit test asserts no
egress when the flag is off.

If enabled, it would pseudonymize identifiers before anything left the building, and be
reached only from a borderline gate band — never from a confident answer and never from a
clear failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from server.config import settings

log = logging.getLogger("echomind.escalation")

# The band where local retrieval was suggestive but not sufficient. Outside it there is
# nothing to gain: below, there is no signal; above, the local answer already passed.
BORDERLINE_LOW = 0.30
BORDERLINE_HIGH = 0.45

USER_ID_RE = re.compile(r"\bu-[a-z0-9]+\b", re.IGNORECASE)
ACCOUNT_CODE_RE = re.compile(r"\bACC-[A-Z0-9]+\b")
LAB_ID_RE = re.compile(r"\blab-[a-z]\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[^@\s]+@[^@\s]+\.[^@\s]+\b")
BARCODE_RE = re.compile(r"\bBC\d{6}\b")


@dataclass
class Pseudonymized:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)

    def restore(self, text: str) -> str:
        out = text
        for placeholder, original in self.mapping.items():
            out = out.replace(placeholder, original)
        return out


def pseudonymize(text: str, names: list[str] | None = None) -> Pseudonymized:
    """Replace identifiers with stable placeholders. Reversible locally, opaque remotely."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}

    def replace(pattern: re.Pattern[str], kind: str, body: str) -> str:
        def sub(match: re.Match[str]) -> str:
            original = match.group(0)
            for placeholder, existing in mapping.items():
                if existing == original:
                    return placeholder
            counters[kind] = counters.get(kind, 0) + 1
            placeholder = f"<{kind}_{counters[kind]}>"
            mapping[placeholder] = original
            return placeholder

        return pattern.sub(sub, body)

    out = text
    out = replace(EMAIL_RE, "EMAIL", out)
    out = replace(USER_ID_RE, "USER", out)
    out = replace(ACCOUNT_CODE_RE, "ACCOUNT", out)
    out = replace(LAB_ID_RE, "LAB", out)
    out = replace(BARCODE_RE, "SAMPLE", out)

    for name in sorted(names or [], key=len, reverse=True):
        if name and name in out:
            counters["NAME"] = counters.get("NAME", 0) + 1
            placeholder = f"<NAME_{counters['NAME']}>"
            mapping[placeholder] = name
            out = out.replace(name, placeholder)

    return Pseudonymized(text=out, mapping=mapping)


def should_escalate(gate_score: float) -> bool:
    """Routing condition. False whenever the feature is off — which is the default."""
    if not settings.escalation_enabled:
        return False
    if not settings.frontier_base_url:
        log.warning("ESCALATION_ENABLED=true but FRONTIER_BASE_URL is empty; not escalating")
        return False
    return BORDERLINE_LOW <= gate_score < BORDERLINE_HIGH


def escalate(question: str, context: str, names: list[str] | None = None) -> str:
    """The egress point. Refuses to run unless explicitly enabled."""
    if not settings.escalation_enabled:
        raise RuntimeError(
            "escalation is disabled (ESCALATION_ENABLED=false); no request was made"
        )
    if not settings.frontier_base_url:
        raise RuntimeError("FRONTIER_BASE_URL is not configured; no request was made")

    safe_question = pseudonymize(question, names)
    safe_context = pseudonymize(context, names)
    log.warning(
        "escalating a pseudonymized question to %s", settings.frontier_base_url
    )

    import httpx

    response = httpx.post(
        settings.frontier_base_url.rstrip("/") + "/chat/completions",
        json={
            "model": "frontier",
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Answer only from the provided context. Identifiers have been "
                        "replaced with placeholders; keep them exactly as they appear."
                    ),
                },
                {
                    "role": "user",
                    "content": f"QUESTION:\n{safe_question.text}\n\nCONTEXT:\n{safe_context.text}",
                },
            ],
        },
        timeout=settings.llm_timeout_s,
    )
    response.raise_for_status()
    answer = response.json()["choices"][0]["message"]["content"]
    # Restore locally so the user sees real identifiers again.
    return safe_context.restore(safe_question.restore(answer))
