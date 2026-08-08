"""Identity and the caller context every enforcement point reads from.

Spec 05: JWT (HS256, JWT_SECRET) with claims
    {sub, role: 'user'|'pi'|'admin', lab_ids: [...], facility_ids: [...], name}

The context is built ONLY from verified claims. Nothing downstream may take a user id,
role or lab from the request body, the prompt, or the model — those are all attacker- or
model-controlled. No anonymous access.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.config import settings

ALGORITHM = "HS256"
DEFAULT_TTL = timedelta(hours=12)

DEV_SECRET = "dev-only-change-me"

# PyJWT warns on every encode/decode that the spec's dev secret is under 32 bytes, which
# would flood token output and demo logs. Suppress the per-call noise and say it once,
# loudly, instead.
try:
    from jwt.warnings import InsecureKeyLengthWarning

    warnings.filterwarnings("ignore", category=InsecureKeyLengthWarning)
except ImportError:  # pragma: no cover - older PyJWT
    pass


def secret_is_insecure() -> bool:
    return settings.jwt_secret == DEV_SECRET or len(settings.jwt_secret) < 32

# Tier ladder from spec 05.
TIER_BY_ROLE = {"user": 1, "pi": 2, "admin": 3}


@dataclass(frozen=True)
class Ctx:
    """Verified caller context."""

    user_id: str
    name: str
    role: str
    lab_ids: tuple[str, ...] = ()
    facility_ids: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def tier(self) -> int:
        return TIER_BY_ROLE.get(self.role, 0)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_pi(self) -> bool:
        return self.role == "pi"

    def can_read_user(self, target_user_id: str) -> bool:
        """Admins: anyone. PIs: themselves (lab membership is checked against the DB by
        the caller, which knows the target's lab). Everyone else: themselves only."""
        return self.is_admin or target_user_id == self.user_id

    def can_read_lab(self, lab_id: str | None) -> bool:
        if self.is_admin:
            return True
        return lab_id is not None and lab_id in self.lab_ids


def mint(
    user_id: str,
    name: str,
    role: str,
    lab_ids: list[str] | tuple[str, ...] = (),
    facility_ids: list[str] | tuple[str, ...] = (),
    ttl: timedelta = DEFAULT_TTL,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "name": name,
        "role": role,
        "lab_ids": list(lab_ids),
        "facility_ids": list(facility_ids),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode(token: str) -> Ctx:
    """Verify signature + expiry and build the context. Raises HTTPException(401)."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],  # never accept 'none' or an RS/HS confusion
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    role = claims.get("role", "user")
    if role not in TIER_BY_ROLE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid role claim")

    return Ctx(
        user_id=str(claims["sub"]),
        name=str(claims.get("name", claims["sub"])),
        role=role,
        lab_ids=tuple(claims.get("lab_ids") or ()),
        facility_ids=tuple(claims.get("facility_ids") or ()),
        raw=claims,
    )


_bearer = HTTPBearer(auto_error=False)


def require_ctx(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Ctx:
    """FastAPI dependency. Every route and the MCP server depend on this."""
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode(creds.credentials)


def ctx_from_request(request: Request) -> Ctx:
    """Same check for non-DI call sites (SSE handlers, the MCP transport)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    return decode(token)
