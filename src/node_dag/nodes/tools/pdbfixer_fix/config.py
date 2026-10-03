from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import ProteinStructure


class PdbfixerFixConfig(BaseToolConfig):
    """Repair each structure with `PDBFixer <https://github.com/openmm/pdbfixer>`_.

    Runs, in order: replace non-standard residues with their standard parents,
    remove heterogens, add missing residues and heavy atoms, then add hydrogens at
    ``ph``. Each step is optional. One structure comes back per structure, as mmCIF,
    keeping the original chain ids, residue numbers and insertion codes. Its
    sequence is read from its protein residues, chains joined in file order.

    Added residues and atoms are modelled, not observed: PDBFixer builds them and
    minimises them with OpenMM. Treat their coordinates as a guess. Where an atom
    has alternate locations, only the first is kept.

    Args:
        replace_nonstandard: Replace modified residues, such as MSE, with their
            standard parent, such as MET.
        remove_heterogens: ``none`` keeps every non-polymer residue. ``keep_water``
            removes ligands and ions but keeps water. ``all`` removes water too.
        add_missing_residues: Build residues in the deposited sequence that have no
            coordinates, e.g. disordered loops. Off by default, since they are
            modelled.
        add_missing_atoms: Add heavy atoms missing from residues that are present.
        ph: Add hydrogens for this pH, or leave them off with ``None``.
        seed: The seed for placing added atoms, so a structure fixes the same way
            every time.
    """

    name: Literal["pdbfixer_fix"] = "pdbfixer_fix"
    replace_nonstandard: bool = True
    remove_heterogens: Literal["none", "keep_water", "all"] = "none"
    add_missing_residues: bool = False
    add_missing_atoms: bool = True
    ph: float | None = Field(default=7.0, ge=0.0, le=14.0)
    seed: int = 0
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"structure": ProteinStructure}
    output: ClassVar = ProteinStructure
    intents: ClassVar = (
        "fix or clean a protein structure with PDBFixer",
        "add missing atoms, residues or hydrogens to a structure",
        "replace non-standard residues and remove heterogens or water",
        "prepare a structure for simulation or analysis",
    )
    when_to_use: ClassVar = (
        "Use to prepare a deposited or predicted structure before analysis or "
        "simulation: complete residues, add hydrogens, strip ligands or water."
    )
    when_not_to_use: ClassVar = (
        "Do not use to predict a structure from a sequence; use esmfold2_fold. Do "
        "not treat added residues as observed: they are modelled."
    )
