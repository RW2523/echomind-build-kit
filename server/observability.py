"""Tracing with a console fallback.

Spec 06: every graph node and tool call is a span, one trace per chat turn. With
LANGFUSE_ENABLED=false — the default — the same structure is written as JSON lines to
logs/traces.jsonl, and the admin page reads whichever source is live.

Tracing must never be able to break a chat turn: every path here swallows its own errors.
"""

from __future__ import annotations

import functools
import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any

from server.config import settings

log = logging.getLogger("echomind.trace")

TRACE_FILE = settings.logs_dir / "traces.jsonl"

# Tags spec 06 asks for on every span, so the admin view has a stable shape.
DEFAULT_TAGS = {
    "route": None,
    "gate_result": None,
    "sql_valid": None,
    "action_kind": None,
    "escalated": False,
}


class Span:
    def __init__(self, name: str, trace_id: str, parent: str | None, **attrs: Any) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = uuid.uuid4().hex[:12]
        self.parent_id = parent
        self.attrs: dict[str, Any] = {**DEFAULT_TAGS, **attrs}
        self.started = time.time()
        self.error: str | None = None

    def set(self, **attrs: Any) -> None:
        self.attrs.update(attrs)

    def to_record(self) -> dict[str, Any]:
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(self.started)),
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "duration_ms": round((time.time() - self.started) * 1000, 1),
            "error": self.error,
            **{k: v for k, v in self.attrs.items() if v is not None or k in DEFAULT_TAGS},
        }


class Tracer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local = threading.local()
        self._langfuse = None
        self._langfuse_tried = False

    # --- Langfuse (optional) ---------------------------------------------------

    def _client(self):
        if not settings.langfuse_enabled:
            return None
        if not self._langfuse_tried:
            self._langfuse_tried = True
            try:
                from langfuse import Langfuse

                self._langfuse = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
                log.info("Langfuse tracing enabled (%s)", settings.langfuse_host)
            except Exception as exc:
                log.warning("Langfuse unavailable (%s); falling back to traces.jsonl", exc)
                self._langfuse = None
        return self._langfuse

    # --- span plumbing ---------------------------------------------------------

    @property
    def _stack(self) -> list[Span]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    @contextmanager
    def trace(self, name: str, **attrs: Any):
        """Start a new root trace (one per chat turn)."""
        trace_id = uuid.uuid4().hex[:16]
        with self._span(name, trace_id, None, **attrs) as span:
            yield span

    @contextmanager
    def span(self, name: str, **attrs: Any):
        """A child span. Becomes a root if nothing is open (e.g. a bare tool call)."""
        parent = self._stack[-1] if self._stack else None
        trace_id = parent.trace_id if parent else uuid.uuid4().hex[:16]
        with self._span(name, trace_id, parent.span_id if parent else None, **attrs) as s:
            yield s

    @contextmanager
    def _span(self, name: str, trace_id: str, parent_id: str | None, **attrs: Any):
        span = Span(name, trace_id, parent_id, **attrs)
        self._stack.append(span)
        try:
            yield span
        except Exception as exc:
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._stack.pop()
            self._emit(span)

    def _emit(self, span: Span) -> None:
        record = span.to_record()
        try:
            client = self._client()
            if client is not None:
                client.create_event(
                    name=span.name,
                    metadata=record,
                    trace_context={"trace_id": span.trace_id},
                )
        except Exception as exc:
            log.debug("langfuse emit failed: %s", exc)

        # Always write the local line: it is the fallback source AND the demo's evidence
        # that a turn was traced, whether or not Langfuse is up.
        try:
            with self._lock:
                TRACE_FILE.parent.mkdir(parents=True, exist_ok=True)
                with TRACE_FILE.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            log.debug("trace write failed: %s", exc)

    # --- readback for the admin page ------------------------------------------

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not TRACE_FILE.exists():
            return []
        try:
            lines = TRACE_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= limit:
                break
        return out

    @property
    def sink(self) -> str:
        return "langfuse" if settings.langfuse_enabled else "console"


tracer = Tracer()


def traced_tool(name: str):
    """Wrap a tool handler in a span (spec 06: every tool call traced).

    `functools.wraps` matters here beyond tidiness: the MCP server builds each tool's
    JSON schema from `inspect.signature` and `get_type_hints` of the handler, and wraps
    copies `__annotations__` and sets `__wrapped__` so both still see the real signature
    rather than (*args, **kwargs).
    """

    def wrap(fn):
        @functools.wraps(fn)
        def inner(ctx, *args, **kwargs):
            with tracer.span(f"tool.{name}", user_id=getattr(ctx, "user_id", None)) as span:
                try:
                    result = fn(ctx, *args, **kwargs)
                except Exception as exc:
                    span.set(tool_error=type(exc).__name__, code=getattr(exc, "code", None))
                    raise
                if isinstance(result, dict):
                    span.set(
                        row_count=result.get("row_count"),
                        action_kind=result.get("kind"),
                        sql_valid=("executed_sql" in result) or None,
                    )
                return result

        return inner

    return wrap
