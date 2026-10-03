from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Rna


class DnaTranscribeConfig(BaseToolConfig):
    """Transcribe a coding DNA sequence to its mRNA, by swapping T for U.

    The input is the coding strand, so transcription is only a change of alphabet: the
    bases stay in the same order and only T becomes U. Use this when the goal asks to
    transcribe DNA, or for the RNA or mRNA that a coding sequence makes.

    Do not use it to get a protein: ``dna_to_protein`` does that in one step. If the
    input is the template strand rather than the coding strand, reverse complement it
    with ``dna_reverse_complement`` first. ``rna_back_transcribe`` is the inverse.
    """

    name: Literal["dna_transcribe"] = "dna_transcribe"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Rna
    example: ClassVar = "sequence=ATGTCGTAA -> rna AUGUCGUAA, with U in place of T"
    intents: ClassVar = (
        "transcribe DNA to RNA",
        "turn DNA into RNA",
        "make RNA from a DNA sequence",
        "get the mRNA a coding sequence makes",
        "convert a coding strand to messenger RNA",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks to transcribe DNA, or for the RNA or mRNA of a coding "
        "sequence. The input is the coding strand, so this is only a change of "
        "alphabet: the bases keep their order and T becomes U."
    )
    when_not_to_use: ClassVar = (
        "Do not use to get a protein: dna_to_protein does that in one step from DNA. "
        "Do not use it on the template strand without reverse complementing first. Its "
        "output is RNA, which no DNA port accepts, so rna_back_transcribe is needed to "
        "come back."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
