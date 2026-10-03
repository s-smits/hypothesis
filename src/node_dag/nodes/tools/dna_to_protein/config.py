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
    intents: ClassVar = (
        "translate DNA to protein",
        "convert a coding DNA sequence to its amino acid sequence",
        "read off the protein a sequence codes for",
        "compare the proteins of two DNA sequences",
    )
    when_to_use: ClassVar = (
        "Use when the goal needs the amino acid sequence itself: to produce the "
        "protein, or to compare the proteins of two sequences by translating each."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal stays in DNA: its output is an amino acid sequence, "
        "which no DNA port accepts, so putting it in the middle of a recoding pipeline "
        "is a dead end. Do not use it to check a synonymous change numerically "
        "(dna_atom_score takes a reference and raises on a protein change), and do not "
        "use it to reach RNA (dna_transcribe does that)."
    )
