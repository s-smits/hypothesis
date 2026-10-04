from node_dag.dna import longest_inverted_repeat, longest_repeat, repeat_fraction
from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.repeat_score.config import RepeatScoreConfig
from node_dag.types import NucleicAcid, Score


class RepeatScore(BaseNode[RepeatScoreConfig]):
    """Measure each sequence's longest direct and inverted repeats, and its extent."""

    def run(self, sequence: list[NucleicAcid]) -> list[dict[str, Score]]:
        """Return the repeat scores of each sequence."""
        return [self._scores(s) for s in sequence]

    def _scores(self, s: NucleicAcid) -> dict[str, Score]:
        # Reverse complement is a DNA operation, so read an RNA input as its DNA.
        seq = s.sequence.replace("U", "T")
        return {
            "max_repeat": Score(value=float(longest_repeat(seq))),
            "max_inverted_repeat": Score(value=float(longest_inverted_repeat(seq))),
            "repeat_fraction": Score(
                value=repeat_fraction(seq, self.config.min_length)
            ),
        }
