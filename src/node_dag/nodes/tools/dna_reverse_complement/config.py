from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class DnaReverseComplementConfig(BaseToolConfig):
    """Give the opposite strand of each DNA sequence, read 5' to 3'."""

    name: Literal["dna_reverse_complement"] = "dna_reverse_complement"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "reverse complement a DNA sequence",
        "get the opposite / antisense / template strand 5' to 3'",
        "read a gene encoded on the minus strand",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for the reverse complement, the opposite or antisense "
        "strand, or to flip a sequence onto the other strand."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal asks only for the complement in the same direction; "
        "use dna_complement instead."
    )
