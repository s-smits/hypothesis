from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class MutateSynonymousConfig(BaseToolConfig):
    """Make a new DNA sequence from each one, by swapping random codons for synonymous ones.

    Every amino acid stays the same. The same seed on the same sequence gives the same
    result, and each sequence gets its own random choices. The new sequences replace the
    old, so they have no scores. Sequences that come out the same merge into one.

    Args:
        seed: Seeds the random choice of codons and their replacements.
        count: How many codons to swap in each sequence. A sequence with fewer codons
            that have a synonym has all of them swapped.
        variants_per_sequence: How many mutated candidate sequences to produce for each
            input sequence. Default is 1. Increase (e.g. 5-20) when searching or
            optimizing to generate a candidate pool.
    """

    name: Literal["mutate_synonymous"] = "mutate_synonymous"
    seed: int
    count: int = Field(default=1, ge=1)
    variants_per_sequence: int = Field(
        default=1,
        ge=1,
        description="How many mutated variants to generate per input sequence.",
    )
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "generate variant DNA sequences with synonymous codons",
        "create candidate mutants that preserve protein sequence",
        "mutate DNA without altering translated amino acids",
        "explore codon space for optimization or directed evolution",
    )
    when_to_use: ClassVar = (
        "Use whenever the goal asks to find, improve, optimize, increase, or lower a "
        "property (such as atom count or expression) while keeping the protein unchanged. "
        "Set variants_per_sequence > 1 (e.g. 10) to create a candidate pool to score and filter."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal is only to measure, score, or translate the given "
        "sequences without generating new ones."
    )
