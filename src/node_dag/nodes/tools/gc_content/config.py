from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import NucleicAcid, Score


class GcContentConfig(BaseScoreConfig):
    """Score each sequence's GC content, overall and in sliding windows.

    A gene's worst window usually matters before its average does: synthesis
    providers and expression hosts both flag local GC extremes. A sequence
    shorter than ``window`` is one window.

    Scores:
        gc: The G and C fraction over the whole sequence.
        gc_min, gc_max: The lowest and highest GC fraction of any ``window``-
            base window.
        gc_deviation: The furthest any window's GC sits from ``target``. Filter
            on it with ``at_most`` to keep windows near the target.

    Args:
        window: The window length in bases.
        target: The GC fraction ``gc_deviation`` is measured against.
    """

    name: Literal["gc_content"] = "gc_content"
    window: int = Field(ge=1)
    target: float = Field(default=0.5, ge=0.0, le=1.0)
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": NucleicAcid}
    output: ClassVar = {
        "gc": Score,
        "gc_min": Score,
        "gc_max": Score,
        "gc_deviation": Score,
    }
    intents: ClassVar = (
        "score GC content of a sequence",
        "measure windowed GC minima, maxima and deviation",
        "check GC constraints for synthesis or expression",
    )
    when_to_use: ClassVar = (
        "Use to score or filter sequences by overall or windowed GC content, "
        "including reading back a gc_target_recode result."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal is about codon usage or motifs rather than "
        "base composition."
    )
