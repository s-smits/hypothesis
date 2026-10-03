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
    intents: ClassVar = (
        "generate a variant DNA sequence with synonymous codons",
        "explore codon space for a sequence that scores better",
        "make a candidate mutant that keeps the protein unchanged",
        "vary a sequence blindly when no particular codon is named",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks to search for a sequence that scores better on some "
        "measure (atom count, expression) while keeping the protein, and names no "
        "particular codon to change. Vary the seed to get different candidates and "
        "score each."
    )
    when_not_to_use: ClassVar = (
        "Do not use to remove, avoid or replace a named codon. It picks both the codon "
        "it changes and the replacement at RANDOM, so it cannot be aimed: it may leave "
        "the codon you want gone untouched, and may even swap another codon for it. "
        "Use recode_codons to remove specific named codons. Do not use it either when "
        "the goal is only to measure, score or convert the sequence in hand."
    )
