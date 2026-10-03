from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Score


class DnaAtomScoreConfig(BaseToolConfig):
    """Score a DNA sequence by its total atom count, a lower score being better.

    The sequence must code for the same protein as the reference, so a sequence with a
    non-synonymous substitution is an error, not a score.
    """

    name: Literal["dna_atom_score"] = "dna_atom_score"
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna, "reference": Dna}
    output: ClassVar = Score
    example: ClassVar = (
        "sequence=ATGTCGTAA, reference=ATGAGCTAA -> score 297 "
        "(both code for MS*); reference=ATGGGGTAA raises, MG* is a different protein"
    )
