from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class MutateSynonymousConfig(BaseToolConfig):
    """Make a new DNA sequence by swapping random codons for synonymous ones.

    Every amino acid stays the same. The same seed on the same sequence gives the same
    result.

    Args:
        seed: Seeds the random choice of codons and their replacements.
        count: How many codons to swap. At most the number of codons.
    """

    name: Literal["mutate_synonymous"] = "mutate_synonymous"
    seed: int
    count: int = Field(default=1, ge=1)
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    example: ClassVar = (
        "sequence=ATGTCGTAA with seed=0, count=1 -> ATGTCGTGA: the codon and its "
        "replacement are chosen at random, so this cannot target a given codon"
    )
