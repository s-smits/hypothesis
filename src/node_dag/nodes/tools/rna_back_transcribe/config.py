from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Rna


class RnaBackTranscribeConfig(BaseToolConfig):
    """Turn an RNA sequence back into its coding DNA, by swapping U for T.

    The exact inverse of ``dna_transcribe``: the bases stay in the same order and only
    U becomes T. Use this when the value in hand is RNA and the goal needs a node that
    takes DNA, such as translating to protein, scoring, or mutating, since a port typed
    ``Dna`` will not accept RNA. Do not use it when the value is already DNA.
    """

    name: Literal["rna_back_transcribe"] = "rna_back_transcribe"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Rna}
    output: ClassVar = Dna
    example: ClassVar = "sequence=AUGUCGUAA -> dna ATGTCGTAA, with T in place of U"
    # Deliberately phrased from the RNA side. Naming DNA in every intent made this node
    # out-score dna_transcribe on "turn DNA into RNA" -- the opposite direction -- because
    # the search counts words and cannot see which way a conversion runs. The DNA-port
    # explanation lives in when_to_use, which is weighted lower.
    intents: ClassVar = (
        "back transcribe RNA to DNA",
        "convert an RNA sequence back",
        "undo a transcription",
    )
    when_to_use: ClassVar = (
        "Use when the value in hand is RNA and the next node takes DNA, such as "
        "translating, scoring, recoding or mutating: a port typed Dna will not accept "
        "RNA, and this is the only node that converts back."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the value is already DNA: its input port only accepts RNA, so "
        "an extra step here will not wire. It is a change of alphabet, not a "
        "translation or a strand flip."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
