from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.mrna_fold_energy.config import MrnaFoldEnergyConfig
from node_dag.types import NucleicAcid, Score

ZERO = {
    "mfe": Score(value=0.0),
    "ensemble_energy": Score(value=0.0),
    "mfe_per_base": Score(value=0.0),
}


class MrnaFoldEnergy(BaseNode[MrnaFoldEnergyConfig]):
    """Fold each sequence whole with ViennaRNA and report its energies."""

    def run(self, sequence: list[NucleicAcid]) -> list[dict[str, Score]]:
        """Return the folding energies of each sequence."""
        # Importing ViennaRNA is only worth it when a step actually runs.
        import RNA

        results = []
        for s in sequence:
            mrna = s.sequence.replace("T", "U")
            if not mrna:
                results.append(dict(ZERO))
                continue
            fc = RNA.fold_compound(mrna)
            _, mfe = fc.mfe()
            # Rescaling the Boltzmann factors on the MFE keeps the partition
            # function from overflowing on a long sequence.
            fc.exp_params_rescale(mfe)
            _, ensemble = fc.pf()
            results.append(
                {
                    "mfe": Score(value=mfe),
                    "ensemble_energy": Score(value=ensemble),
                    "mfe_per_base": Score(value=mfe / len(mrna)),
                }
            )
        return results
