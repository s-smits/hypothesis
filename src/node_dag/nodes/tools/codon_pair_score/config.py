from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.dna import CODON_TABLE
from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score


class CodonPairScoreConfig(BaseScoreConfig):
    """Score each coding sequence by the bias of its adjacent codon pairs.

    Codon pair bias — pairs over- or under-represented relative to what their
    codons' frequencies predict — is a recoding objective a single-codon score
    cannot see. The score is the mean of ``pair_weights`` over the sequence's
    in-frame adjacent pairs, with ``missing`` for pairs absent from the table.
    A sequence with fewer than two codons scores 0.

    Whether high or low is better depends on the table's convention; check it
    before filtering. The table is part of this config's hash, so its
    provenance belongs in the experiment record.

    Scores:
        codon_pair: The mean pair weight across the sequence.

    Args:
        pair_weights: Weight per six-base codon pair: ``"ATGGCT"`` is the
            weight of ATG followed by GCT.
        missing: The weight given to pairs absent from the table.
    """

    name: Literal["codon_pair_score"] = "codon_pair_score"
    pair_weights: dict[str, float]
    missing: float = 0.0
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {"codon_pair": Score}
    intents: ClassVar = (
        "score adjacent codon pair bias",
        "measure codon context effects beyond single-codon usage",
        "evaluate codon pair optimisation of coding sequences",
    )
    when_to_use: ClassVar = (
        "Use to score or filter coding sequences by a codon pair table, an "
        "objective codon_adaptation cannot express."
    )
    when_not_to_use: ClassVar = (
        "Do not use for single-codon usage (codon_adaptation) or without a "
        "pair table to score against."
    )

    @field_validator("pair_weights")
    @classmethod
    def _check_pairs(cls, v: dict[str, float]) -> dict[str, float]:
        for pair in v:
            if (
                len(pair) != 6
                or pair[:3] not in CODON_TABLE
                or pair[3:] not in CODON_TABLE
            ):
                raise ValueError(f"Not two upper-case DNA codons: {pair!r}")
        return v
