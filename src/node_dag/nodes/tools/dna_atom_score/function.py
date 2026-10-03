from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.types import Dna, Score


class DnaAtomScore(BaseNode[DnaAtomScoreConfig]):
    """Return the atom count of ``sequence``, if it has the protein of ``reference``."""

    def run(self, sequence: Dna, reference: Dna) -> Score:
        """Return the atom count, or raise if an amino acid differs from the reference."""
        if sequence.protein() != reference.protein():
            raise ValueError(
                f"Not synonymous with the reference: {sequence.protein()} "
                f"vs {reference.protein()}"
            )
        return Score(value=sequence.atom_count())
