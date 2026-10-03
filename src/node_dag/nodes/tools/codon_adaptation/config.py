from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.dna import check_codon_weights
from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score


class CodonAdaptationConfig(BaseScoreConfig):
    """Score each coding sequence by how well its codons match a usage table.

    The codon adaptation index is the geometric mean of each codon's relative
    adaptiveness: the codon's weight over the highest weight among its amino
    acid's synonyms. 1.0 means every codon is the table's favourite. Codons
    whose amino acid has no positive weight in the table are left out of the
    mean, as are stop codons and partial trailing codons; a sequence with
    nothing left to score gets 0. The same table scores a ``codon_optimise``
    run, so keep them paired.

    Scores:
        cai: The codon adaptation index, 0 to 1. Filter on it with ``at_least``.

    Args:
        codon_weights: Usage weight per codon, e.g. per-thousand frequency or
            relative adaptiveness from a codon usage table. The table is part
            of this config's hash, so its provenance belongs in the experiment
            record.
    """

    name: Literal["codon_adaptation"] = "codon_adaptation"
    codon_weights: dict[str, float]
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {"cai": Score}
    intents: ClassVar = (
        "score codon adaptation index (CAI) against a usage table",
        "measure how well codons match an organism's usage",
        "evaluate codon optimality of coding sequences",
    )
    when_to_use: ClassVar = (
        "Use to score or filter coding sequences by codon usage, and to read "
        "back how close a codon_optimise run came to the table's optimum."
    )
    when_not_to_use: ClassVar = (
        "Do not use on non-coding sequence, or without a usage table to score against."
    )

    @field_validator("codon_weights")
    @classmethod
    def _check_weights(cls, v: dict[str, float]) -> dict[str, float]:
        return check_codon_weights(v)
