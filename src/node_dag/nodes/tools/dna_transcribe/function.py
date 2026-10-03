from Bio.Seq import Seq

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_transcribe.config import DnaTranscribeConfig
from node_dag.types import Dna, Rna


class DnaTranscribe(BaseNode[DnaTranscribeConfig]):
    """Transcribe each coding DNA sequence to mRNA."""

    def run(self, sequence: list[Dna]) -> list[Rna]:
        """Return one RNA sequence per DNA sequence."""
        return [Rna(sequence=str(Seq(s.sequence).transcribe())) for s in sequence]
