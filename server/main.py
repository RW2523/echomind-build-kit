"""FastAPI application entrypoint. The MCP server is mounted alongside at /mcp."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import __version__
from server.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("echomind")

from server.api import actions as actions_api  # noqa: E402
from server.api import admin as admin_api  # noqa: E402
from server.api import chat as chat_api  # noqa: E402
from server.api import dataspaces as dataspaces_api  # noqa: E402
from server.api import demo_login as demo_login_api  # noqa: E402
from server.api import library as library_api  # noqa: E402
from server.api import me as me_api  # noqa: E402
from server.api import tools as tools_api  # noqa: E402
from server.api import uploads as uploads_api  # noqa: E402
from server.mcp.server import http_app as mcp_http_app  # noqa: E402

_mcp_app = mcp_http_app(path="/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from server.auth import secret_is_insecure

    if secret_is_insecure():
        log.warning(
            "JWT_SECRET is the dev default or shorter than 32 bytes — fine for the local "
            "demo, never for a deployment. Set a strong JWT_SECRET in .env."
        )
    # The mounted MCP app owns a session manager that must be started with the process.
    async with _mcp_app.router.lifespan_context(app):
        yield


app = FastAPI(
    title="EchoMind Local",
    version=__version__,
    description="Fully local, audited AI assistant for the Infinity X core-facility platform.",
    lifespan=lifespan,
)

# The Vite dev server runs on a different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STARTED_AT = datetime.now(UTC).isoformat()

app.include_router(chat_api.router)
app.include_router(tools_api.router)
app.include_router(actions_api.router)
app.include_router(uploads_api.router)
app.include_router(admin_api.router)
app.include_router(dataspaces_api.router)
app.include_router(library_api.router)
app.include_router(me_api.router)
app.include_router(demo_login_api.router)
app.mount("/mcp", _mcp_app)


@app.get("/healthz")
def healthz() -> dict:
    """Liveness probe. Deliberately does not touch the database so it answers during boot."""
    return {"ok": True}


@app.get("/readyz")
def readyz() -> dict:
    """Readiness: reports on the dependencies the demo actually needs."""
    from server.db import ping
    from server.mcp.tools import TOOLS

    db_ok = ping()
    return {
        "ok": db_ok,
        "database": db_ok,
        "tools": len(TOOLS),
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "embed_model": settings.embed_model,
        "escalation_enabled": settings.escalation_enabled,
        "langfuse_enabled": settings.langfuse_enabled,
        "version": __version__,
        # When this process started, so a test driver can notice it is exercising a
        # server older than the code under test. Two golden-suite passes in one session
        # blessed a router rule the server had never loaded; the failures surfaced two
        # restarts later, attributed to whatever change happened to be in hand then.
        "started_at": STARTED_AT,
    }
