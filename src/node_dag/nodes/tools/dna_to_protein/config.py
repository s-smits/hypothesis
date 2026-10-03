from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, Dna


class DnaToProteinConfig(BaseToolConfig):
    """Convert a DNA sequence to its corresponding amino acid sequence.

    Translates the DNA sequence into its protein representation using the
    standard genetic code.
    """

    name: Literal["dna_to_protein"] = "dna_to_protein"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = AminoAcidSequence
    example: ClassVar = "sequence=ATGTCGTAA -> amino_acid_sequence MS*"
