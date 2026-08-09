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


# Which request shape this endpoint wants for schema-constrained decoding:
#   "openai" — {"json_schema": {"name": ..., "schema": {...}, "strict": true}}  (OpenAI, vLLM)
#   "raw"    — {"json_schema": {...the schema itself...}}                        (TensorRT-LLM)
#   None     — not probed yet;  False — schema decoding unavailable, use json_object
# Engines genuinely disagree here, and TensorRT-LLM given the wrapper form returns 200
# with grammar-mangled output rather than an error — so this is probed, not assumed.
_schema_style: str | bool | None = None

# Deliberately shaped like the judge schemas — an object wrapping a length-bounded array
# of objects. A flat {"ok": true} probe is useless: TensorRT-LLM handles that fine under
# either wrapper and only mangles the grammar once nesting is involved, which is exactly
# where the real calls live.
PROBE_SCHEMA = {
    "title": "probe",
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 2,
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "ok": {"type": "boolean"}},
                "required": ["id", "ok"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _probe_is_valid(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    items = parsed.get("items")
    if not isinstance(items, list) or len(items) != 2:
        return False
    return all(isinstance(i, dict) and "id" in i and "ok" in i for i in items)


def _wrap_schema(schema: dict[str, Any], style: str) -> dict[str, Any]:
    if style == "raw":
        return {"type": "json_schema", "json_schema": schema}
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.get("title", "response"),
                        "schema": schema, "strict": True},
    }


def _probe_schema_style() -> str | bool:
    """Ask the endpoint for one trivially-constrained object and see what comes back.

    Costs one request per process. Checking that the reply *parses* matters as much as
    that it returns 200: the wrong wrapper shape is accepted and silently corrupts the
    grammar rather than erroring.
    """
    global _schema_style
    for style in ("openai", "raw"):
        try:
            resp = client().chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content":
                           "Return two items with ids 1 and 2, both ok."}],
                temperature=0.0,
                max_tokens=120,
                response_format=_wrap_schema(PROBE_SCHEMA, style),
                **({"extra_body": settings.extra_body} if settings.extra_body else {}),
            )
            content = (resp.choices[0].message.content or "").strip()
            parsed = json.loads(content)
            if _probe_is_valid(parsed):
                log.info("structured output: json_schema (%s style)", style)
                _schema_style = style
                return style
            log.info("structured output: %s style returned %r; trying next", style, content[:60])
        except Exception as exc:  # noqa: BLE001
            log.info("structured output: %s style rejected (%s)", style, type(exc).__name__)
    log.warning("structured output: no working json_schema style; using json_object")
    _schema_style = False
    return False


def _response_format(schema: dict[str, Any] | None, json_mode: bool) -> dict[str, Any] | None:
    """Strongest structured-output mode this endpoint supports.

    A JSON *schema* is qualitatively different from JSON *mode*: the decoder can only
    emit tokens the grammar allows, so a judge physically cannot skip a required field
    or return the wrong number of verdicts. Asking politely in the prompt — which is
    what json_object amounts to — is what let the judge drop verdicts silently.
    """
    global _schema_style
    mode = settings.llm_structured_output
    if mode == "off":
        return None

    if schema is not None and mode in ("auto", "json_schema"):
        if _schema_style is None:
            _probe_schema_style()
        if _schema_style:
            return _wrap_schema(schema, str(_schema_style))

    if json_mode and mode in ("auto", "json_schema", "json_object"):
        return {"type": "json_object"}
    return None


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 700,
    json_mode: bool = False,
    schema: dict[str, Any] | None = None,
) -> str:
    global _schema_style

    kwargs: dict[str, Any] = {
        "model": model or settings.llm_model,
        "messages": messages,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": max_tokens,
    }
    extra = settings.extra_body
    if extra:
        kwargs["extra_body"] = extra

    fmt = _response_format(schema, json_mode)
    if fmt:
        kwargs["response_format"] = fmt

    try:
        resp = client().chat.completions.create(**kwargs)
    except Exception as exc:
        if fmt is None:
            raise
        if fmt["type"] == "json_schema":
            # Endpoint does not implement schema-constrained decoding — remember, so the
            # probe costs one request per process rather than one per call.
            _schema_style = False
            log.warning("json_schema unsupported by endpoint (%s); falling back", exc)
            kwargs.pop("response_format")
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = client().chat.completions.create(**kwargs)
            except Exception as exc2:
                log.warning("json_object also unsupported (%s); retrying plain", exc2)
                kwargs.pop("response_format", None)
                resp = client().chat.completions.create(**kwargs)
        else:
            log.warning("json_mode unsupported by endpoint (%s); retrying plain", exc)
            kwargs.pop("response_format")
            resp = client().chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def structured_output_mode() -> str:
    """What the endpoint actually turned out to support. For the admin surface."""
    if settings.llm_structured_output == "off":
        return "off"
    if _schema_style is None:
        return f"{settings.llm_structured_output} (unprobed)"
    if _schema_style is False:
        return "json_object"
    return f"json_schema/{_schema_style}"


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
    schema: dict[str, Any] | None = None,
) -> Any:
    """Ask for JSON and parse it. Returns `default` rather than raising when given one.

    Pass `schema` wherever the shape matters — it is enforced by the decoder when the
    endpoint supports it, not merely requested in the prompt.
    """
    raw = chat(messages, model=model, temperature=0.0, max_tokens=max_tokens,
               json_mode=True, schema=schema)
    try:
        return extract_json(raw)
    except ValueError:
        if default is not None:
            log.warning("falling back to default for unparseable JSON: %r", raw[:200])
            return default
        raise
