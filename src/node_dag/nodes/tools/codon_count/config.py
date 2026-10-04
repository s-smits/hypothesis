from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, Field

from node_dag.dna import codon_set
from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score


class CodonCountConfig(BaseScoreConfig):
    """Score each DNA sequence by how many of its in-frame codons are in ``codons``.

    Scores:
        count: In-frame codons that are one of ``codons``. 0 means none remain.

    Args:
        codons: The codons to count, three bases each. At least one.
    """

    name: Literal["codon_count"] = "codon_count"
    codons: Annotated[tuple[str, ...], Field(min_length=1), AfterValidator(codon_set)]
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {"count": Score}
    intents: ClassVar = (
        "count how many times a codon occurs in a DNA sequence",
        "check that a codon is absent from a recoded sequence",
    )
    when_to_use: ClassVar = (
        "Use to prove a codon is gone: score the sequences, then filter on the count "
        "being at most 0. Counts in-frame codons only."
    )
