from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import ProteinStructure, StructureAlignment


class TmalignConfig(BaseToolConfig):
    """Superpose each structure on one reference structure with TM-align.

    Runs `TM-align <https://zhanggroup.org/TM-align/>`_ through
    `tmtools <https://github.com/jvkersch/tmtools>`_ on the Cα trace of one chain
    from each structure. TM-align builds its own residue alignment from the
    coordinates, so the two chains need neither the same length nor the same
    sequence, and nothing has to be aligned beforehand. One ``structure_alignment``
    comes back per structure on the ``structure`` port, carrying the RMSD over the
    aligned residue pairs, the TM-score under both normalisations, how many pairs
    were aligned and their sequence identity.

    The ``reference`` port takes exactly one structure, the one everything is
    superposed on; a port carrying several is an error, since which of them was the
    reference would not be recorded. As with any tool, an empty port skips the step.

    Only the Cα atoms of protein residues are used, from the first model, and
    waters, ligands and other non-protein residues are left out. A residue with no
    Cα is skipped, so a gappy chain aligns on the residues it does have. A
    non-standard residue keeps its position in the trace and enters the sequence as
    ``X``, which no residue is identical to.

    The RMSD is over the aligned pairs under the TM-score-optimal superposition, so
    it is not a whole-chain RMSD and is only meaningful beside its aligned length;
    see ``StructureAlignment``. The scores say how alike two folds are, not whether
    either is correct, stable or functional.

    Args:
        chain: The author chain id to take from each structure on the ``structure``
            port. Empty means the structure's only protein chain, and a structure
            with more than one is then an error.
        reference_chain: The author chain id to take from the reference, with the
            same meaning when empty.
    """

    name: Literal["tmalign"] = "tmalign"
    chain: str = ""
    reference_chain: str = ""
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"structure": ProteinStructure, "reference": ProteinStructure}
    output: ClassVar = StructureAlignment
    intents: ClassVar = (
        "compare two protein structures of different length or sequence",
        "RMSD between two protein structures",
        "TM-align two structures and get the TM-score",
        "structural similarity of a design to a reference fold",
        "check whether a redesigned sequence keeps the reference fold",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks how close a structure is to a reference structure, "
        "e.g. whether designs from protlib_design or folds from esmfold2_fold keep "
        "a target fold. It is the right node when the two proteins differ in length "
        "or sequence, since the residue alignment is found from the coordinates."
    )
    when_not_to_use: ClassVar = (
        "Do not use to compare sequences without structures; fold them first. It "
        "measures one chain against one chain, so it does not score a whole "
        "complex or an interface: use chain_contacts for interface residues. It "
        "reports entities, not score columns, so top_k, at_most and "
        "beats_reference cannot filter on its RMSD or TM-score."
    )
