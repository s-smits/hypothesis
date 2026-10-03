from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, Dna


class DnaToProteinConfig(BaseToolConfig):
    """Translate each DNA sequence to its amino acid sequence, with the standard genetic code."""

    name: Literal["dna_to_protein"] = "dna_to_protein"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = AminoAcidSequence
