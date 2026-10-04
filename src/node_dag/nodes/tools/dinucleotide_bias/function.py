from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.dinucleotide_bias.config import DinucleotideBiasConfig
from node_dag.types import NucleicAcid, Score


class DinucleotideBias(BaseNode[DinucleotideBiasConfig]):
    """Score each sequence's dinucleotide occurrences against its base composition."""

    def run(self, sequence: list[NucleicAcid]) -> list[dict[str, Score]]:
        """Return the odds ratio and frequency of each sequence."""
        return [self._scores(s) for s in sequence]

    def _scores(self, s: NucleicAcid) -> dict[str, Score]:
        # The dinucleotides are DNA, so an RNA input is read as its DNA spelling.
        seq = s.sequence.replace("U", "T")
        pairs = len(seq) - 1
        if pairs < 1:
            return {"odds_ratio": Score(value=0.0), "frequency": Score(value=0.0)}
        observed = sum(
            seq.startswith(d, i)
            for d in self.config.dinucleotides
            for i in range(pairs)
        )
        # Expected from the sequence's own base frequencies: pairs * f(X) * f(Y).
        expected = sum(
            pairs * (seq.count(d[0]) / len(seq)) * (seq.count(d[1]) / len(seq))
            for d in self.config.dinucleotides
        )
        return {
            "odds_ratio": Score(value=observed / expected if expected else 0.0),
            "frequency": Score(value=observed / pairs),
        }
