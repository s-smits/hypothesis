from typing import ClassVar, Literal

from pydantic import Field, model_validator

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import ProteinContacts, ProteinStructure


class ChainContactsConfig(BaseToolConfig):
    """Find the residue contacts between the protein chains of each structure.

    Loads each mmCIF structure with `mdtraj <https://www.mdtraj.org>`_ and reports
    every pair of protein residues on different chains that come within ``cutoff``
    of each other, with their distance in ångströms. Waters, ligands and other
    non-protein residues are left out. One ``protein_contacts`` comes back per
    structure, even when it has no contacts.

    Residues are named ``chain:res_name:res_num:insertion_code`` with the author
    chain, number and insertion code, as a PDB file shows them, e.g. ``H:ALA:52:A``.

    Distances are within the deposited coordinates only: a crystal's unit cell is
    ignored, so symmetry mates do not make contacts. A structure with more than one
    model, such as an NMR ensemble, is rejected rather than reduced to one model.
    Where an atom has alternate locations, the first listed is used.

    Args:
        cutoff: The largest distance, in ångströms, that counts as a contact.
        scheme: How a residue pair's distance is measured. ``closest-heavy``: the
            closest pair of non-hydrogen atoms. ``closest``: the closest pair of
            any atoms. ``ca``: between the alpha carbons.
        chains_a: Chain ids on one side of the interface. Empty means every chain.
        chains_b: Chain ids on the other side. Empty means every chain not in
            ``chains_a``. A named chain missing from a structure is an error.
    """

    name: Literal["chain_contacts"] = "chain_contacts"
    cutoff: float = Field(default=5.0, gt=0.0)
    scheme: Literal["closest-heavy", "closest", "ca"] = "closest-heavy"
    chains_a: tuple[str, ...] = ()
    chains_b: tuple[str, ...] = ()
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"structure": ProteinStructure}
    output: ClassVar = ProteinContacts
    intents: ClassVar = (
        "find contacts between protein chains in a structure",
        "list interface residues of a protein complex",
        "residue pairs within a distance across chains",
        "protein-protein interface contacts with mdtraj",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks which residues touch across chains of a complex, "
        "e.g. an antibody-antigen or protein-protein interface, with distances."
    )
    when_not_to_use: ClassVar = (
        "Do not use on single-chain structures, such as esmfold2_fold output: it "
        "finds no intra-chain contacts. Do not use for protein-ligand contacts."
    )

    @model_validator(mode="after")
    def _check_chains(self) -> "ChainContactsConfig":
        if both := set(self.chains_a) & set(self.chains_b):
            raise ValueError(f"Chains {sorted(both)} are on both sides.")
        if self.chains_b and not self.chains_a:
            raise ValueError("Set chains_a when setting chains_b.")
        return self
