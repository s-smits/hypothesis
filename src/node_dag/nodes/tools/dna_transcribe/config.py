from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Rna


class DnaTranscribeConfig(BaseToolConfig):
    """Transcribe each coding DNA sequence to its mRNA, by swapping T for U."""

    name: Literal["dna_transcribe"] = "dna_transcribe"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Rna
    intents: ClassVar = (
        "transcribe DNA to RNA / mRNA",
        "convert a coding strand to messenger RNA",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks to transcribe DNA, or for the RNA or mRNA a coding "
        "sequence makes."
    )
    when_not_to_use: ClassVar = (
        "Do not use to translate to protein (use dna_to_protein), or when the input is "
        "the template strand; reverse complement it first with dna_reverse_complement."
    )
