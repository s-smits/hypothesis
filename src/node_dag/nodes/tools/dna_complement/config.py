from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class DnaComplementConfig(BaseToolConfig):
    """Swap each base of each DNA sequence for its pair (A with T, C with G), in the same order."""

    name: Literal["dna_complement"] = "dna_complement"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "complement a DNA sequence",
        "get the complementary strand without reversing it",
        "pair each base with its Watson-Crick partner",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for the complement of DNA sequences, read in the same "
        "direction as the input."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal asks for the reverse complement or the opposite strand "
        "read 5' to 3'; use dna_reverse_complement instead."
    )
