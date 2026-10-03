from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dna_transcribe.config import DnaTranscribeConfig
from node_dag.types import Dna, Rna

TO_RNA = str.maketrans("T", "U")


class DnaTranscribe(BaseNode[DnaTranscribeConfig]):
    """Transcribe the coding DNA ``sequence`` to mRNA."""

    def run(self, sequence: Dna) -> Rna:
        """Return the mRNA: the same bases, with U for T."""
        return Rna(sequence=sequence.sequence.translate(TO_RNA))
