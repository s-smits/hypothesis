"""Where to watch a node's work while it runs, for nodes that run it elsewhere.

A node that offloads to a GPU on Modal knows the URL of that run only from inside
``run``, and only while it runs. It calls :func:`report`, and whoever is running the
node decides what to do with the link: an activity saves it so the UI can offer it,
and a test or a script listens to nothing at all, so ``report`` is free to call.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel


class Link(BaseModel):
    """Somewhere to watch a step's work.

    Args:
        label: What to call it, e.g. ``Modal``.
        url: Where it is.
    """

    label: str
    url: str


_sink: ContextVar[Callable[[Link], None] | None] = ContextVar("_sink", default=None)


def report(label: str, url: str) -> None:
    """Offer a link to whoever is running this node. Does nothing if nobody listens."""
    if sink := _sink.get():
        sink(Link(label=label, url=url))


@contextmanager
def collecting(sink: Callable[[Link], None]) -> Iterator[None]:
    """Send every link a node reports in this block to ``sink``, as it reports it."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
