from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from node_dag.nodes.base import BaseDecisionConfig, Category
from node_dag.types import Dna


class GcInRangeConfig(BaseDecisionConfig):
    """Yes when the GC fraction of a sequence stays inside a range.

    GC content is the fraction of bases that are G or C. It matters for recoding
    because synonymous codons differ mostly in their third base, and that base is
    often G or C in one choice and A or T in another -- so a recoding cannot help but
    move it. Too far either way and the sequence is hard to synthesise, folds
    differently, or sits oddly for its host.

    ``window`` is the part worth setting. A sequence whose GC averages 50% can still
    carry a GC-rich patch that defeats synthesis, and the global figure hides it.
    With ``window`` set, every window of that many bases must be in range, not just
    the whole sequence. Commercial synthesis guidance works in 60-base windows.

    This is a coarse guard. It says nothing about whether the sequence still
    translates, which ostir_expression measures directly, nor about the first few
    codons, where start_region_composition applies a sharper rule.

    Args:
        low: Lowest GC fraction allowed, 0 to 1. Vendors reject below about 0.3.
        high: Highest GC fraction allowed, 0 to 1. Above about 0.7 synthesis suffers.
        window: How many bases each checked window spans. 0 checks the sequence as a
            whole. 60 matches the window synthesis guidance uses. A window longer than
            the sequence falls back to checking the whole thing.
    """

    name: Literal["gc_in_range"] = "gc_in_range"
    low: float = Field(ge=0.0, le=1.0)
    high: float = Field(ge=0.0, le=1.0)
    window: int = Field(default=0, ge=0)
    categories = (Category.FILTER,)
    inputs: ClassVar = {"sequence": Dna}
    forwards = "sequence"
    example: ClassVar = (
        "ATGGCCGGCGCA with low=0.4 high=0.8 -> yes, forwarding the DNA on <step>.yes. "
        "ATGTCTTCTGCTTAA with low=0.4 high=0.8 -> no: it is 33% GC. With window=6 the "
        "same limits also reject a sequence that averages 50% but holds a GC-rich "
        "stretch, which is what defeats synthesis."
    )
    intents: ClassVar = (
        "keep GC content inside a viable window",
        "check the GC fraction of a sequence",
        "reject a sequence that would be hard to synthesise",
        "check a sequence can be synthesised",
        "find a GC-rich patch that would break synthesis",
        "check base composition after recoding",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks about GC content, base composition or whether a "
        "sequence can be synthesised. Set window to 60 to catch local GC-rich patches, "
        "which the figure for the whole sequence hides and which are what actually "
        "break synthesis."
    )
    when_not_to_use: ClassVar = (
        "Do not use it as a proxy for expression: ostir_expression measures translation "
        "initiation directly, and start_region_composition carries the sharper rule for "
        "the first few codons. Do not use it to check which codons a sequence holds, "
        "which is codons_absent."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1

    @model_validator(mode="after")
    def _check(self) -> Self:
        """``low`` must not exceed ``high``, or nothing could ever pass."""
        if self.low > self.high:
            raise ValueError(f"low {self.low} is above high {self.high}")
        return self
