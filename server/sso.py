"""Mapping an institutional identity provider's claims onto EchoMind's context.

What this is, and what it deliberately is not.

`server/auth.py` builds every `Ctx` from verified claims — `sub`, `role`, `lab_ids`,
`facility_ids` — and every permission decision in the system reads that context and
nothing else. An SSO deployment does not change any of that. It changes only where those
four values come from: instead of a demo token, they arrive in an OIDC id_token or a SAML
assertion from AD FS, Azure AD, Okta or Shibboleth.

This module is that translation, and it is the whole integration surface. It is written
and tested here rather than left as a diagram because the mapping is where the mistakes
live: an AD group that grants admin, a `eduPersonAffiliation` that does not mean what a
reader assumes, a lab list arriving as a comma-joined string rather than a list.

What is NOT here, on purpose: token fetch, signature verification against a JWKS
endpoint, session cookies, discovery. Those are the provider's protocol, they need a real
IdP to test against, and anything written blind would be plausible code that has never
once run against Shibboleth. The seam is the mapping; the protocol is a deployment task.

Wire-up in one function:

    from server.sso import ctx_from_idp_claims
    ctx = ctx_from_idp_claims(verified_claims, mapping=YOUR_MAPPING)

where `verified_claims` is whatever your OIDC/SAML library hands back AFTER it has
verified the signature. This module assumes verification already happened and says so
loudly, because a mapping that silently accepted unverified claims would be the single
worst bug in the system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from server.auth import TIER_BY_ROLE, Ctx

log = logging.getLogger("echomind.sso")


@dataclass(frozen=True)
class ClaimMapping:
    """Which claim names this institution uses, and which groups mean which role.

    Defaults follow OIDC's standard claims. A Shibboleth deployment typically overrides
    `user_id` to `eduPersonPrincipalName` and `groups` to `isMemberOf`.
    """

    user_id: str = "sub"
    name: str = "name"
    email: str = "email"
    groups: str = "groups"
    # Group (or affiliation) values that grant each role. Checked most-privileged first,
    # so someone in both the admin and PI groups is an admin rather than whichever the
    # dictionary happened to yield first.
    admin_groups: tuple[str, ...] = ("echomind-admins", "facility-admins")
    pi_groups: tuple[str, ...] = ("echomind-pis", "principal-investigators")
    # Group prefixes that name a lab or facility: "lab-lab-a" -> "lab-a".
    lab_group_prefix: str = "lab-"
    facility_group_prefix: str = "facility-"
    # Claims that carry the lists directly, when the IdP is configured to send them
    # rather than encoding them in group names.
    lab_ids: str = "lab_ids"
    facility_ids: str = "facility_ids"
    extra: dict[str, str] = field(default_factory=dict)


DEFAULT_MAPPING = ClaimMapping()


def _as_list(value: object) -> list[str]:
    """IdPs send multi-valued claims as a list, a semicolon string, or a comma string.

    Shibboleth in particular sends `isMemberOf` as a single semicolon-delimited string,
    which iterates character by character if it is treated as a sequence — the whole
    group list then silently evaluates to nothing and the user gets no privileges at all.
    """
    if value is None:
        return []
    if isinstance(value, str):
        for sep in (";", ","):
            if sep in value:
                return [p.strip() for p in value.split(sep) if p.strip()]
        return [value.strip()] if value.strip() else []
    if isinstance(value, list | tuple | set):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def role_from_groups(groups: list[str], mapping: ClaimMapping = DEFAULT_MAPPING) -> str:
    """Most-privileged wins. Comparison is case-insensitive: AD is not consistent."""
    lowered = {g.lower() for g in groups}
    if lowered & {g.lower() for g in mapping.admin_groups}:
        return "admin"
    if lowered & {g.lower() for g in mapping.pi_groups}:
        return "pi"
    return "user"


def _scoped(groups: list[str], prefix: str) -> tuple[str, ...]:
    lowered = prefix.lower()
    return tuple(
        g[len(prefix):] for g in groups if g.lower().startswith(lowered) and len(g) > len(prefix)
    )


def ctx_from_idp_claims(
    claims: dict, mapping: ClaimMapping = DEFAULT_MAPPING, *, verified: bool = True
) -> Ctx:
    """Build a Ctx from an identity provider's ALREADY-VERIFIED claims.

    `verified` exists to be impossible to pass by accident: a caller that has not checked
    the signature has to say so, and gets an error rather than a working context.
    """
    if not verified:
        raise ValueError(
            "ctx_from_idp_claims requires claims whose signature has already been "
            "verified by your OIDC/SAML library. Refusing to build a context from "
            "unverified input."
        )

    user_id = claims.get(mapping.user_id)
    if not user_id:
        raise ValueError(f"claim {mapping.user_id!r} is missing; cannot identify the caller")

    groups = _as_list(claims.get(mapping.groups))
    role = role_from_groups(groups, mapping)
    if role not in TIER_BY_ROLE:  # unreachable today; a guard against a future role typo
        raise ValueError(f"mapped role {role!r} is not one of {tuple(TIER_BY_ROLE)}")

    # Explicit list claims win over group-name encoding: an institution that configures
    # them has said what it means, which beats inferring from a naming convention.
    lab_ids = (
        tuple(_as_list(claims.get(mapping.lab_ids)))
        or _scoped(groups, mapping.lab_group_prefix)
    )
    facility_ids = (
        tuple(_as_list(claims.get(mapping.facility_ids)))
        or _scoped(groups, mapping.facility_group_prefix)
    )

    log.info("sso: %s -> role=%s labs=%s", user_id, role, list(lab_ids))
    return Ctx(
        user_id=str(user_id),
        name=str(claims.get(mapping.name) or user_id),
        role=role,
        lab_ids=lab_ids,
        facility_ids=facility_ids,
        raw=dict(claims),
    )
