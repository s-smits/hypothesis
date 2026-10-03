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
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
