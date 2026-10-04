from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class TrimToFirstStartConfig(BaseToolConfig):
    """Trim each DNA sequence to start at its first ATG, dropping the 5' leader.

    The first ATG anywhere in the sequence becomes the first codon, in whatever
    frame it was found, and any trailing partial codon is cut so the output length
    is a multiple of 3. A sequence that already starts with ATG is unchanged apart
    from that cut. A sequence with no ATG has no ORF to keep and is dropped from
    the output list rather than passed through.
    """

    name: Literal["trim_to_first_start"] = "trim_to_first_start"
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "trim a sequence to the ORF that starts at its first ATG",
        "drop a 5' leader or UTR so the coding sequence starts with ATG",
        "put the start codon at the position an expression score reads",
    )
    when_to_use: ClassVar = (
        "Use when a sequence carries a 5' leader before its ORF and a downstream "
        "score, such as translation initiation, reads from the first codon."
    )
    when_not_to_use: ClassVar = (
        "Do not use to recode codons in place, or to convert between DNA, RNA "
        "and protein. A sequence with no ATG is dropped, so do not use when "
        "every input must reach the output."
    )
    # Part of the cache key and the config hash. Raise it when a change to run() changes
    # its results: `python -m temporal.scaffold_node trim_to_first_start --bump`.
    version: ClassVar[int] = 1
