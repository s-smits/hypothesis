from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class DnaReverseComplementConfig(BaseToolConfig):
    """Give the opposite strand of a DNA sequence, read 5' to 3'.

    The complement, reversed: each base is swapped for its pair and the order is
    flipped, because the opposite strand runs antiparallel to this one. Use this when
    the goal asks for the reverse complement, the opposite, antisense or template
    strand, or to read a gene that is encoded on the minus strand. Use
    ``dna_complement`` when the goal asks only for the complement in the same
    direction.

    The sequence keeps its length, so it still holds whole codons, but the frame is a
    different frame: the protein of the reverse complement is not the input's protein.
    """

    name: Literal["dna_reverse_complement"] = "dna_reverse_complement"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    example: ClassVar = (
        "sequence=ATGTCGTAA -> TTACGACAT: the complement TACAGCATT read backwards"
    )
    intents: ClassVar = (
        "reverse complement a DNA sequence",
        "get the opposite, antisense or template strand read 5' to 3'",
        "read a gene that is encoded on the minus strand",
        "flip a sequence onto the other strand",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks for the reverse complement, or for the opposite, "
        "antisense or template strand, or when a coding sequence has to be recovered "
        "from the template strand before translating or transcribing it."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal asks only for the complement in the same direction; "
        "that is dna_complement. The reverse complement is a different reading frame, "
        "so do not use it when the protein must be preserved."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
