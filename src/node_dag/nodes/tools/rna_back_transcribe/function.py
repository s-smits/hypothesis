from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.types import Dna, Rna


class RnaBackTranscribe(BaseNode[RnaBackTranscribeConfig]):
    """Turn each RNA sequence back into coding DNA."""

    def run(self, sequence: list[Rna]) -> list[Dna]:
        """Return one DNA sequence per RNA sequence."""
        return [Dna(sequence=str(Seq(s.sequence).back_transcribe())) for s in sequence]
