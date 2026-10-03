from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.types import Dna, Rna

TO_DNA = str.maketrans("U", "T")


class RnaBackTranscribe(BaseNode[RnaBackTranscribeConfig]):
    """Turn the RNA ``sequence`` back into coding DNA."""

    def run(self, sequence: Rna) -> Dna:
        """Return the coding DNA: the same bases, with T for U."""
        return Dna(sequence=sequence.sequence.translate(TO_DNA))
