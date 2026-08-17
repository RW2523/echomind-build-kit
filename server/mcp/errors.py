"""The uniform tool error object: {code, message, hint}."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """Raised by tool handlers. Serialized identically wherever it surfaces."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "hint": self.hint}}


# Tier denial. The message is deliberately constant: it must not reveal whether the
# target resource exists (spec 02 "Errors", spec 05 required tests).
FORBIDDEN_MESSAGE = "You do not have access to this resource."
FORBIDDEN_HINT = "Ask the core facility admin if you believe you should have access."


def forbidden() -> ToolError:
    return ToolError("forbidden", FORBIDDEN_MESSAGE, FORBIDDEN_HINT)


def missing_or_not_yours(what: str = "record") -> ToolError:
    """For a lookup that deliberately will not say WHICH of the two it is.

    A sample the caller cannot see and a sample that does not exist have to be answered
    identically, or the barcode space can be enumerated by anyone with an account. That
    is the right call and it stays. What was wrong was the wording: asked to track
    "SMP-0001" — a barcode of a shape this platform has never used, the real ones being
    BC1000xx — the caller was told "You do not have access to this resource", which
    accuses them of reaching for someone else's data when they had simply mistyped.
    Naming both possibilities gives away nothing and points at the likelier one.
    """
    return ToolError(
        "forbidden",
        f"I can't show you that {what} — either there is no such {what} or it is not "
        "one of yours.",
        "Check the identifier first; if it is right, the core facility admin can look.",
    )


def not_found(what: str = "resource") -> ToolError:
    return ToolError("not_found", f"No such {what}.", "Check the identifier and try again.")


def invalid_params(message: str, hint: str = "") -> ToolError:
    return ToolError("invalid_params", message, hint)


def sql_rejected(message: str, hint: str = "") -> ToolError:
    return ToolError("sql_rejected", message, hint)
