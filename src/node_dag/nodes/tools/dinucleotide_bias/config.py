from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import NucleicAcid, Score


class DinucleotideBiasConfig(BaseScoreConfig):
    """Score how over- or under-represented a sequence's dinucleotides are.

    Dinucleotide composition is an objective neither ``codon_adaptation`` nor
    ``codon_pair_score`` can express: the first sees one codon at a time, the
    second works in frame on codon pairs, and dinucleotide bias runs across the
    whole sequence regardless of frame, including the pairs that straddle a
    codon boundary. CpG and UpA depletion is the usual target.

    The odds ratio is the standard one: observed occurrences of the configured
    dinucleotides over the number expected from the sequence's own base
    composition, so 1.0 means as often as chance predicts and below 1.0 means
    depleted. Occurrences overlap, so ``CGCG`` has three CG. RNA inputs are
    read as their DNA, so the same dinucleotides apply to both.

    Scores:
        odds_ratio: Observed over expected, pooled across the configured
            dinucleotides. Filter with ``at_most`` to keep depleted sequences.
            A sequence of fewer than two bases, or one where the expected count
            is zero because a base is absent, scores 0.
        frequency: Observed occurrences per adjacent base pair, 0 to 1. Unlike
            the odds ratio this does not adjust for base composition, so a
            change in GC content moves it on its own.

    Args:
        dinucleotides: The pairs to count, upper-case A, C, G, T, e.g.
            ``("CG",)`` for CpG or ``("CG", "TA")`` for CpG and UpA together.
    """

    name: Literal["dinucleotide_bias"] = "dinucleotide_bias"
    dinucleotides: tuple[str, ...]
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": NucleicAcid}
    output: ClassVar = {"odds_ratio": Score, "frequency": Score}
    intents: ClassVar = (
        "score CpG or UpA dinucleotide bias",
        "measure dinucleotide over- or under-representation",
        "evaluate dinucleotide depletion of a recoded sequence",
    )
    when_to_use: ClassVar = (
        "Use to score or filter sequences on dinucleotide composition, an "
        "objective the in-frame codon scores cannot see."
    )
    when_not_to_use: ClassVar = (
        "Do not use for in-frame codon pairs (codon_pair_score), overall base "
        "composition (gc_content) or fixed motifs (motif_count)."
    )

    @field_validator("dinucleotides")
    @classmethod
    def _check_dinucleotides(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("No dinucleotides given.")
        for pair in v:
            if len(pair) != 2 or set(pair) - set("ACGT"):
                raise ValueError(f"Not two upper-case DNA bases: {pair!r}")
        if len(set(v)) != len(v):
            raise ValueError(f"Repeated dinucleotide in {v!r}.")
        return v
