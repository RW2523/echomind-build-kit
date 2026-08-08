"""FastMCP server exposing the 15 tools.

Mounted alongside FastAPI at /mcp (and runnable standalone on MCP_PORT for an external
MCP client). Identity comes from the Authorization header on every call and is turned
into a verified Ctx before the handler runs — the MCP layer adds no privileges of its
own and holds no session state.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, get_type_hints

import anyio.to_thread
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools import FunctionTool

from server.auth import Ctx, decode
from server.config import settings
from server.mcp.errors import ToolError
from server.mcp.tools import TOOLS, ToolSpec

log = logging.getLogger("echomind.mcp")

mcp: FastMCP = FastMCP(
    name="echomind-infinityx",
    instructions=(
        "Tools for the Infinity X core-facility platform. Read tools return facts from "
        "the platform; write tools never execute — they return a pending action that a "
        "human must approve. Facts in answers must come from these results only."
    ),
)


def _ctx_from_headers() -> Ctx:
    # FastMCP strips `authorization` from get_http_headers() by default (it is on the
    # "do not forward downstream" list); it has to be asked for explicitly.
    headers = get_http_headers(include={"authorization"}) or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ToolError(
            "unauthenticated",
            "This tool requires a bearer token.",
            "Send Authorization: Bearer <jwt>.",
        )
    return decode(token)


def _make_wrapper(spec: ToolSpec):
    """Expose a handler with its own signature minus `ctx`, which we derive server-side."""
    sig = inspect.signature(spec.handler)
    params = [p for name, p in sig.parameters.items() if name != "ctx"]
    # Resolve against the handler's own module globals: the tools module uses
    # `from __future__ import annotations`, so its annotations are strings that would not
    # resolve in this module. Pydantic reads __annotations__, not __signature__.
    hints = get_type_hints(spec.handler)

    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        # Resolve identity here, on the event loop: the HTTP headers live in a contextvar
        # that is not visible from FastMCP's worker thread. The handler itself is
        # blocking (psycopg), so it is then offloaded rather than stalling the loop.
        ctx = _ctx_from_headers()
        log.info("mcp tool=%s caller=%s", spec.name, ctx.user_id)
        try:
            return await anyio.to_thread.run_sync(
                functools.partial(spec.handler, ctx, **kwargs)
            )
        except ToolError as exc:
            # Surface the uniform error object rather than a transport-level failure, so
            # the model sees {code, message, hint} and can explain it.
            return exc.to_dict()

    wrapper.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    wrapper.__annotations__ = {
        p.name: hints.get(p.name, Any) for p in params
    } | {"return": dict[str, Any]}
    wrapper.__name__ = spec.name
    doc = [spec.description, "", f"Tier: {spec.tier}."]
    if spec.write:
        doc.append("Write tool: returns a pending action; nothing changes until approved.")
    if spec.params:
        doc += ["", "Parameters:"] + [f"  {k}: {v}" for k, v in spec.params.items()]
    wrapper.__doc__ = "\n".join(doc)
    return wrapper


for _spec in TOOLS.values():
    mcp.add_tool(
        FunctionTool.from_function(
            _make_wrapper(_spec),
            name=_spec.name,
            tags={"write" if _spec.write else "read", _spec.tier},
        )
    )


def http_app(path: str = "/"):
    return mcp.http_app(path=path)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log.info("MCP server on :%s with %d tools", settings.mcp_port, len(TOOLS))
    mcp.run(transport="http", host="0.0.0.0", port=settings.mcp_port)


if __name__ == "__main__":
    main()
