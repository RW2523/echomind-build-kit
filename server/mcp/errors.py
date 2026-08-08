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


def not_found(what: str = "resource") -> ToolError:
    return ToolError("not_found", f"No such {what}.", "Check the identifier and try again.")


def invalid_params(message: str, hint: str = "") -> ToolError:
    return ToolError("invalid_params", message, hint)


def sql_rejected(message: str, hint: str = "") -> ToolError:
    return ToolError("sql_rejected", message, hint)
