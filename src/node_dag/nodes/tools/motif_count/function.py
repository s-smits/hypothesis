from itertools import groupby

from node_dag.dna import motif_hits, reverse_complement
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.motif_count.config import MotifCountConfig
from node_dag.types import NucleicAcid, Score


class MotifCount(BaseNode[MotifCountConfig]):
    """Count each sequence's motif occurrences and longest homopolymer run."""

    def run(self, sequence: list[NucleicAcid]) -> list[dict[str, Score]]:
        """Return the motif and homopolymer counts of each sequence."""
        sites = set(self.config.motifs)
        if self.config.both_strands:
            sites |= {reverse_complement(m) for m in self.config.motifs}
        return [self._scores(s, sites) for s in sequence]

    def _scores(self, s: NucleicAcid, sites: set[str]) -> dict[str, Score]:
        # The motifs are DNA, so an RNA input is counted as its DNA spelling.
        seq = s.sequence.replace("U", "T")
        homopolymer = max((len(list(run)) for _, run in groupby(s.sequence)), default=0)
        return {
            "motifs": Score(value=float(len(motif_hits(seq, sites)))),
            "max_homopolymer": Score(value=float(homopolymer)),
        }
