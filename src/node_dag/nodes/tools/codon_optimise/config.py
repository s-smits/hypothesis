from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.dna import check_codon_weights
from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class CodonOptimiseConfig(BaseToolConfig):
    """Rewrite each coding sequence with synonymous codons chosen by usage weight.

    Every amino acid stays the same; only the spelling changes. ``most_frequent``
    takes the highest-weighted synonym, the classic codon optimisation, and
    ``least_frequent`` takes the lowest, for deoptimisation. ``weighted_sample``
    draws each codon with probability proportional to its weight, a harmonising
    middle ground; it is seeded per sequence, so a sequence's recoding does not
    depend on the rest of the batch.

    A codon whose synonyms all weigh the same, including all zero or all absent
    from the table, is left alone: with no information there is nothing to
    optimise. Pair with ``codon_adaptation`` to score how far a sequence sits
    from the table's optimum.

    Args:
        codon_weights: The usage weight of each codon, e.g. per-thousand
            frequency or relative adaptiveness from a codon usage table. The
            table is part of this config's hash, so its provenance belongs in
            the experiment record.
        strategy: How to choose among synonyms.
        seed: Seeds ``weighted_sample``. Ignored by the other strategies.
    """

    name: Literal["codon_optimise"] = "codon_optimise"
    codon_weights: dict[str, float]
    strategy: Literal["most_frequent", "least_frequent", "weighted_sample"] = (
        "most_frequent"
    )
    seed: int = 0
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "codon-optimise sequences for an organism's codon usage",
        "replace codons with the most or least frequent synonyms",
        "harmonise codon usage with a weighted sample of synonyms",
        "recode sequences toward a codon usage table",
    )
    when_to_use: ClassVar = (
        "Use when the goal gives a codon usage table or asks for codon "
        "optimisation, deoptimisation or harmonisation while keeping the "
        "protein unchanged."
    )
    when_not_to_use: ClassVar = (
        "Do not use without a usage table to aim at; for random synonymous "
        "variation use mutate_synonymous or resample_synonymous, and to remove "
        "named codons use a targeted recoding node."
    )

    @field_validator("codon_weights")
    @classmethod
    def _check_weights(cls, v: dict[str, float]) -> dict[str, float]:
        return check_codon_weights(v)
