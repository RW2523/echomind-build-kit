"""Every prompt, with a version derived from its own text.

A trace that says "the gate refused this" is only half an answer six weeks later; the
other half is which prompt was in force when it did. Langfuse calls this prompt
versioning and expects a name and a version string on each generation.

The version is a hash of the prompt itself rather than a number someone remembers to
bump. A hand-maintained version is wrong exactly when it matters most — someone tweaks a
sentence to fix one eval case, forgets the bump, and two runs that disagree both claim to
be v3. Content addressing cannot drift: change a character and the version changes.

Registration is explicit, so `prompt_registry()` is the list of prompts the system
actually has, not whatever happened to be interned at import time.
"""

from __future__ import annotations

import hashlib

_REGISTRY: dict[str, str] = {}


def version_of(text: str) -> str:
    """A short, stable id for exactly this prompt text."""
    return "p" + hashlib.blake2b(text.strip().encode(), digest_size=4).hexdigest()


def register(name: str, text: str) -> str:
    """Record a prompt and return its version. Call at import, next to the prompt."""
    version = version_of(text)
    _REGISTRY[name] = version
    return version


def prompt_registry() -> dict[str, str]:
    """name -> version, for the admin console and for a trace to attach."""
    return dict(_REGISTRY)


def ensure_registered() -> dict[str, str]:
    """Import the modules that own prompts, so the registry is complete.

    Imported lazily and inside the function because these modules import the LLM client,
    and the admin console should not drag that in just to list versions.
    """
    from server.agent import (  # noqa: F401
        action,
        faithfulness,
        generate,
        rewrite,
        router,
    )

    return prompt_registry()
