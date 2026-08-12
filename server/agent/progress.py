"""Real progress, reported from the places where the work actually happens.

The stream used to invent this. `/chat/stream` emitted three fixed stages on a 0.4-second
timer — "understanding the question", "checking what you have access to", "verifying
against sources" — whether or not any of that had occurred, and the UI rendered them as a
completed checklist. A turn that was refused before retrieval still showed "verifying
against sources" ticked off.

That is a small lie in the one place this product cannot afford one. The whole claim is
that an answer is verified or it is not given; a progress trail that says verification
happened when it did not is the same category of error as a confident wrong answer, just
wearing a spinner. So a stage is now emitted only by the code that has just done the thing
it names, and a turn that skips a step shows no line for it.

Deliberately best-effort and never load-bearing: a sink that raises, or no sink at all
(the non-streaming /chat endpoint, tests, the demo script), must not change the answer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

log = logging.getLogger("echomind.progress")

_sink: ContextVar[Callable[[str], None] | None] = ContextVar("progress_sink", default=None)


@contextmanager
def reporting_to(sink: Callable[[str], None]) -> Iterator[None]:
    """Route this turn's progress to `sink`. Restores the previous sink on the way out."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit(stage: str) -> None:
    """Report a step that has just completed. Safe to call from anywhere, always."""
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(stage)
    except Exception:  # a broken pipe on the client must not fail the turn
        log.debug("progress sink rejected %r", stage, exc_info=True)
