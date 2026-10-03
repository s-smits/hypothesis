from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Rna


class RnaBackTranscribeConfig(BaseToolConfig):
    """Turn each RNA sequence back into its coding DNA, by swapping U for T."""

    name: Literal["rna_back_transcribe"] = "rna_back_transcribe"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Rna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "back transcribe RNA / mRNA to DNA",
        "convert RNA to its coding DNA sequence",
        "use RNA inputs with tools that take DNA",
    )
    when_to_use: ClassVar = (
        "Use when the inputs are RNA and the goal needs a DNA tool, such as translating "
        "to protein, scoring, or mutating."
    )
    when_not_to_use: ClassVar = "Do not use when the inputs are already DNA."
