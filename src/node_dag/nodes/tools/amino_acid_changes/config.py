from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Score


class AminoAcidChangesConfig(BaseToolConfig):
    """Count how many amino acids differ between a sequence and a reference.

    Zero means the change was synonymous: the DNA moved and the protein did not. That
    is the whole point of recoding, and until now the only thing that noticed was
    ``dna_atom_score`` raising, which fails a step rather than producing evidence.

    A count is evidence. Wire it into ``at_most`` with a threshold of 0 and the branch
    that decision takes settles "the protein is unchanged" the same way
    ``codons_absent`` settles "no TCG remains" -- so it can back an assertion, which an
    exception never can.

    Sequences of different lengths are compared position by position, and every position
    the shorter one lacks counts as a change.
    """

    name: Literal["amino_acid_changes"] = "amino_acid_changes"
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna, "reference": Dna}
    output: ClassVar = Score
    example: ClassVar = (
        "sequence=ATGTCTTCTGCTTAA, reference=ATGTCGTCAGCTTAA -> score 0: both are MSSA*, "
        "so the recoding was synonymous. sequence=ATGGGGTCAGCTTAA -> score 1, because "
        "the second amino acid became G."
    )
    intents: ClassVar = (
        "check that a change to DNA left the protein alone",
        "prove a recoding was synonymous",
        "count how many amino acids a mutation changed",
        "compare the protein of two DNA sequences",
    )
    when_to_use: ClassVar = (
        "Use whenever the goal says the protein must not change. Score it against the "
        "original sequence and pass the score to at_most with a threshold of 0: the "
        "branch that decision takes is then the evidence that the protein survived, "
        "which an assertion can name."
    )
    when_not_to_use: ClassVar = (
        "Do not use to compare the DNA itself, which codons_absent does, or to measure "
        "size, which dna_atom_score does. It says nothing about whether the recoded "
        "gene still translates well, which is ostir_expression's job."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
