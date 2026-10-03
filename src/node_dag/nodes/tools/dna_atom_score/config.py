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
    intents: ClassVar = (
        "score a DNA sequence by its atom count",
        "count the atoms in a DNA sequence",
        "find a sequence with fewer or more atoms than a reference",
        "check that a recoding kept the protein of a reference sequence",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks to count atoms, or to lower or raise the atom count of "
        "a sequence. It needs a reference to compare against, so it doubles as the "
        "check that a candidate still codes for the reference's protein: a "
        "non-synonymous candidate raises rather than scoring."
    )
    when_not_to_use: ClassVar = (
        "Do not use to score expression or translation rate; that is "
        "ostir_expression. Do not use it as the only check that a named codon is gone: "
        "atom count says nothing about which codons a sequence carries, so use "
        "codons_absent for that."
    )
