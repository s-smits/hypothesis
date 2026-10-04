from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class GcTargetRecodeConfig(BaseToolConfig):
    """Rewrite each coding sequence so its sliding-window GC sits near ``target``.

    Synthesis providers and expression systems dislike GC extremes, and a gene's
    local minima and maxima matter more than its average. Each sweep looks at
    every codon that has a synonym and takes the spelling that most improves the
    objective — worst window deviation first, total deviation as the tie-break —
    until a sweep improves nothing or ``max_passes`` runs out. Every amino acid
    stays the same, and a stop codon is left as it is; the result is a local
    optimum of that objective, not the global one. Score the outcome with
    ``gc_content``.

    Args:
        target: The GC fraction each window should approach, 0 to 1.
        window: The window length in bases. A sequence shorter than it is one
            window, so the node then works on the sequence's overall GC.
        max_passes: How many improving sweeps to allow before stopping.
    """

    name: Literal["gc_target_recode"] = "gc_target_recode"
    version: ClassVar[int] = 2  # 2: a stop codon is no longer swapped.
    target: float = Field(ge=0.0, le=1.0)
    window: int = Field(ge=1)
    max_passes: int = Field(default=10, ge=1)
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "recode a sequence toward a target GC content",
        "flatten GC-rich or AT-rich windows synonymously",
        "normalise local GC content for synthesis or expression",
        "remove GC extremes while keeping the protein",
    )
    when_to_use: ClassVar = (
        "Use when the goal names a GC target or asks to smooth out GC-rich or "
        "AT-rich regions while keeping the protein unchanged."
    )
    when_not_to_use: ClassVar = (
        "Do not use to measure GC content (gc_content), to aim at a codon "
        "usage table (codon_optimise) or for random synonymous variation."
    )
