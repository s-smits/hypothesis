import warnings

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.types import Dna, Score


class OstirExpression(BaseNode[OstirExpressionConfig]):
    """Score the start codon of each sequence, placed after ``config.utr``, with OSTIR."""

    def run(self, sequence: list[Dna]) -> list[dict[str, Score]]:
        """Return the translation initiation rate of each sequence."""
        # Importing ostir pulls in ViennaRNA, so only pay for it when a step runs. It
        # warns that ViennaRNA is missing if the RNAfold binary is not on PATH, but it
        # calls the Python bindings, which the viennarna wheel does install.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=".*missing dependency ViennaRNA.*"
            )
            from ostir import run_ostir

        utr = self.config.utr
        start = len(utr) + 1  # OSTIR counts bases from 1, so the CDS starts here.
        rates = []
        for s in sequence:
            # start and end bracket the one start codon to consider, so OSTIR returns
            # that start alone, or nothing if the first codon does not initiate.
            found = run_ostir(
                utr + s.sequence,
                start=start,
                end=start,
                aSD=self.config.anti_sd,
                name=s.id,
            )
            rate = found[0]["expression"] if found else 0.0
            rates.append({"expression": Score(value=rate)})
        return rates
