"""Dev-only "login as" endpoint for the demo switcher.

The UI needs bearer tokens for the four demo identities. Minting them in the browser
would mean shipping JWT_SECRET to the client, so the server hands them out instead —
but only while the secret is still the documented dev default. Change JWT_SECRET, as any
real deployment must, and these routes stop existing. That interlock is the point: the
convenience cannot survive into an environment where it would matter.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from server.auth import DEV_SECRET, mint
from server.config import settings
from server.demo_identities import DEMO_USERS

log = logging.getLogger("echomind.demo_login")

router = APIRouter(prefix="/demo", tags=["demo"])


def enabled() -> bool:
    """On for a local dev checkout, or wherever it is asked for explicitly.

    The dev-secret check alone made an open demo and a strong secret mutually exclusive:
    rotating the secret so a public URL could not have its tokens forged also removed the
    only way in. DEMO_LOGIN_ENABLED says "yes, this front door is meant to be open",
    which is a different statement from "this secret is a placeholder".
    """
    return settings.demo_login_enabled or settings.jwt_secret == DEV_SECRET


def _guard() -> None:
    if not enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Not found.",
                "hint": "Demo login exists only while JWT_SECRET is the dev default.",
            },
        )


@router.get("/users")
def list_demo_users() -> dict:
    _guard()
    return {
        "users": [
            {
                "handle": handle,
                "id": u["id"],
                "name": u["name"],
                "role": u["role"],
                "lab_ids": u["lab_ids"],
                "blurb": u["blurb"],
            }
            for handle, u in DEMO_USERS.items()
        ]
    }


@router.post("/login/{handle}")
def login_as(handle: str) -> dict:
    _guard()
    user = DEMO_USERS.get(handle.lower())
    if user is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "No such demo user.", "hint": ""},
        )
    log.info("demo login as %s", user["id"])
    return {
        "handle": handle.lower(),
        "token": mint(
            user_id=user["id"], name=user["name"], role=user["role"],
            lab_ids=user["lab_ids"], facility_ids=user["facility_ids"],
        ),
        "user": {
            "id": user["id"], "name": user["name"], "role": user["role"],
            "lab_ids": user["lab_ids"], "blurb": user["blurb"],
        },
    }
