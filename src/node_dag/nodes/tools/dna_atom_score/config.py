from typing import ClassVar, Literal

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score


class DnaAtomScoreConfig(BaseScoreConfig):
    """Score each DNA sequence by its atom count and by how far its protein is from a reference.

    Scores:
        atom_count: Total atoms in the DNA strand: 33 for each A, 31 for each C, 34 for
            each G and 33 for each T. Lower is smaller.
        amino_acid_changes: Amino acid positions that differ from the reference's
            protein. 0 means synonymous, so filter on it to keep the protein unchanged.

    Args:
        reference: The DNA whose protein the sequences should keep.
    """

    name: Literal["dna_atom_score"] = "dna_atom_score"
    reference: Dna
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {"atom_count": Score, "amino_acid_changes": Score}
