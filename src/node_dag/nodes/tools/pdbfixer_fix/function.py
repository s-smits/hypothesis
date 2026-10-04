import io
import random
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from Bio.Data.PDBData import protein_letters_3to1
from openmm import Platform
from openmm.app import PDBxFile, modeller
from pdbfixer import PDBFixer

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.pdbfixer_fix.config import PdbfixerFixConfig
from node_dag.types import ProteinStructure

# Standard amino acids only: PDBFixer's output names its residues with these.
_ONE_LETTER = {
    k: v for k, v in protein_letters_3to1.items() if v in "ACDEFGHIKLMNPQRSTVWY"
}


_SEEDING = threading.Lock()


@contextmanager
def seeded_modeller(seed: int) -> Iterator[None]:
    """Make OpenMM's Modeller draw its random numbers from ``seed``.

    ``Modeller.addHydrogens`` starts hydrogens at positions from the global
    ``random`` module before minimising them, and takes no seed, so without this a
    structure gets different hydrogens on every run. Swap the module it draws from
    for a seeded one. That is process-wide, and the worker runs steps on threads, so
    hold a lock while it is swapped.
    """
    with _SEEDING:
        saved = modeller.random
        modeller.random = random.Random(seed)  # ty: ignore[invalid-assignment]
        try:
            yield
        finally:
            modeller.random = saved


def sequence_of(fixer: PDBFixer) -> str:
    """The one-letter codes of the protein residues, chains joined in file order."""
    return "".join(
        _ONE_LETTER[r.name] for r in fixer.topology.residues() if r.name in _ONE_LETTER
    )


class PdbfixerFix(BaseNode[PdbfixerFixConfig]):
    """Repair each structure with PDBFixer."""

    def run(self, structure: list[ProteinStructure]) -> list[ProteinStructure]:
        """Return one repaired structure per structure, as mmCIF."""
        out = []
        for s in structure:
            with seeded_modeller(self.config.seed):
                out.append(self._fix(s))
        return out

    def _fix(self, s: ProteinStructure) -> ProteinStructure:
        c = self.config
        # The Reference platform gives the same numbers on every machine; a GPU or
        # the multi-threaded CPU platform may not, and would change the result.
        fixer = PDBFixer(
            pdbxfile=io.StringIO(s.structure),
            platform=Platform.getPlatformByName("Reference"),
        )
        fixer.findMissingResidues()
        if not c.add_missing_residues:
            fixer.missingResidues = {}
        if c.replace_nonstandard:
            fixer.findNonstandardResidues()
            fixer.replaceNonstandardResidues()
        if c.remove_heterogens != "none":
            fixer.removeHeterogens(keepWater=c.remove_heterogens == "keep_water")
        fixer.findMissingAtoms()
        if not c.add_missing_atoms:
            fixer.missingAtoms = {}
            fixer.missingTerminals = {}
        fixer.addMissingAtoms(seed=c.seed)
        if c.ph is not None:
            fixer.addMissingHydrogens(pH=c.ph)

        out = io.StringIO()
        PDBxFile.writeFile(fixer.topology, fixer.positions, out, keepIds=True)
        return ProteinStructure(sequence=sequence_of(fixer), structure=out.getvalue())
