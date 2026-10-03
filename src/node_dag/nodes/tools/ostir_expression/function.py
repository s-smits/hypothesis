import warnings

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.types import Dna, Score


class OstirExpression(BaseNode[OstirExpressionConfig]):
    """Score the start codon of ``sequence``, placed after ``config.utr``, with OSTIR."""

    def run(self, sequence: Dna) -> Score:
        """Return the translation initiation rate of the sequence's first codon."""
        # Importing ostir pulls in ViennaRNA, so only pay for it when a step runs. It
        # warns that ViennaRNA is missing when the RNAfold binary is not on PATH, but it
        # calls the Python bindings, which the viennarna wheel does install.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=".*missing dependency ViennaRNA.*"
            )
            from ostir import run_ostir

        # OSTIR counts bases from 1, so the coding sequence starts just past the UTR.
        # Bracketing start and end to that one position asks for that start codon alone,
        # and returns nothing when the first codon does not initiate.
        start = len(self.config.utr) + 1
        found = run_ostir(
            self.config.utr + sequence.sequence,
            start=start,
            end=start,
            aSD=self.config.anti_sd,
            name="sequence",
        )
        return Score(value=float(found[0]["expression"]) if found else 0.0)
