from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class ResampleSynonymousConfig(BaseToolConfig):
    """Redraw every codon of each sequence for a fresh random synonymous spelling.

    Where ``mutate_synonymous`` swaps a fixed number of positions, this resamples
    every codon that has a synonym, choosing uniformly among all the synonyms of
    its amino acid, including the one already there. The protein is unchanged.
    It is the random-synonymous baseline for a recoding experiment: the
    distribution a smarter algorithm has to beat.

    The same seed on the same sequence gives the same result, whatever else is
    in the batch. New sequences replace the old, so they have no scores, and
    sequences that come out the same merge into one.

    Args:
        seed: Seeds each sequence's draws.
        variants_per_sequence: How many resampled sequences to produce per
            input. Increase it to build a candidate pool for best-of-N.
    """

    name: Literal["resample_synonymous"] = "resample_synonymous"
    seed: int
    variants_per_sequence: int = Field(
        default=1,
        ge=1,
        description="How many resampled variants to generate per input sequence.",
    )
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "resample every synonymous codon of a sequence",
        "generate uniform random synonymous sequences",
        "produce a random recoding baseline preserving the protein",
        "build a candidate pool for best-of-N selection",
    )
    when_to_use: ClassVar = (
        "Use for a random synonymous baseline or a large candidate pool, when "
        "every codon should be redrawn rather than a few swapped."
    )
    when_not_to_use: ClassVar = (
        "Do not use to mutate only a few positions (mutate_synonymous), to aim "
        "at a codon usage table (codon_optimise) or to remove named codons."
    )
