from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.mrna_5prime_mfe.config import Mrna5primeMfeConfig
from node_dag.types import NucleicAcid, Score


class Mrna5primeMfe(BaseNode[Mrna5primeMfeConfig]):
    """Fold each sequence's 5' end with ViennaRNA and report the MFE."""

    def run(self, sequence: list[NucleicAcid]) -> list[dict[str, Score]]:
        """Return the minimum free energy of each sequence's folded 5' region."""
        # Importing ViennaRNA is only worth it when a step actually runs.
        import RNA

        results = []
        for s in sequence:
            dna = s.sequence.replace("U", "T")
            mrna = (self.config.utr + dna[: self.config.cds_bases]).replace("T", "U")
            _, mfe = RNA.fold(mrna)
            results.append({"mfe": Score(value=mfe)})
        return results
