from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, Dna


class DnaToProteinConfig(BaseToolConfig):
    """Translate each DNA sequence to its amino acid sequence, with the standard genetic code."""

    name: Literal["dna_to_protein"] = "dna_to_protein"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = AminoAcidSequence
    intents: ClassVar = (
        "translate DNA to protein / amino acid sequence",
        "convert DNA coding sequence to protein",
        "produce translated protein from coding DNA",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks to convert or translate DNA sequences to amino acid "
        "sequences or proteins."
    )
    when_not_to_use: ClassVar = (
        "Do not use for scoring, filtering, or generating synonymous mutants."
    )
