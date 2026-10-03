from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class DnaComplementConfig(BaseToolConfig):
    """Swap each base of a DNA sequence for its pair (A with T, C with G), in order.

    The result is read in the same direction as the input, base for base, so position
    one of the output pairs with position one of the input. That is the complement, not
    the opposite strand: a strand is read 5' to 3', which means the opposite strand runs
    backwards relative to this one. Use ``dna_reverse_complement`` when the goal asks
    for the opposite, antisense or template strand, and this when it asks for the
    complement in the same direction.

    The sequence keeps its length, so it still holds whole codons, but its codons are
    not the input's codons read in reverse; nothing here preserves the protein.
    """

    name: Literal["dna_complement"] = "dna_complement"
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    example: ClassVar = "sequence=ATGTCGTAA -> TACAGCATT, base for base in order"
    intents: ClassVar = (
        "complement a DNA sequence",
        "pair each base with its Watson-Crick partner, in order",
        "get the complementary strand without reversing it",
    )
    when_to_use: ClassVar = (
        "Use only when the goal asks for the complement read in the same direction as "
        "the input, position for position."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal asks for the reverse complement, or the opposite, "
        "antisense or template strand read 5' to 3': a strand is read 5' to 3', so the "
        "opposite strand is this complement reversed. Use dna_reverse_complement. The "
        "output keeps its length but not the input's protein, so do not use it to "
        "transform a coding sequence."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
