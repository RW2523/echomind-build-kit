"""LLM access over an OpenAI-compatible endpoint.

Everything the agent asks a model goes through here. No cloud endpoints appear anywhere
in the core path (golden rule 6); the escalation stub is the only module that may talk to
FRONTIER_BASE_URL, and it is disabled by default.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import OpenAI

from server.config import settings

log = logging.getLogger("echomind.llm")

_client: OpenAI | None = None

JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.llm_base_url,
            api_key="local",
            timeout=settings.llm_timeout_s,
        )
    return _client


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 700,
    json_mode: bool = False,
) -> str:
    kwargs: dict[str, Any] = {
        "model": model or settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = client().chat.completions.create(**kwargs)
    except Exception as exc:
        if json_mode:
            # Not every OpenAI-compatible server implements response_format.
            log.warning("json_mode unsupported by endpoint (%s); retrying plain", exc)
            kwargs.pop("response_format")
            resp = client().chat.completions.create(**kwargs)
        else:
            raise
    return (resp.choices[0].message.content or "").strip()


def extract_json(raw: str) -> Any:
    """Parse JSON from a model reply, tolerating prose or fences around it."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"could not parse JSON from model reply: {raw[:300]!r}")


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    default: Any = None,
    max_tokens: int = 700,
) -> Any:
    """Ask for JSON and parse it. Returns `default` rather than raising when given one."""
    raw = chat(messages, model=model, temperature=0.0, max_tokens=max_tokens, json_mode=True)
    try:
        return extract_json(raw)
    except ValueError:
        if default is not None:
            log.warning("falling back to default for unparseable JSON: %r", raw[:200])
            return default
        raise
