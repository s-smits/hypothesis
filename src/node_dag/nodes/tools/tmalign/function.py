import numpy as np
from tmtools import tm_align

from node_dag.nodes.base import BaseNode

# chain_contacts owns the mmCIF reader that keeps author chain ids and one model.
from node_dag.nodes.tools.chain_contacts.function import load_mmcif
from node_dag.nodes.tools.tmalign.config import TmalignConfig
from node_dag.types import ProteinStructure, StructureAlignment

# TM-align fits a superposition, so a trace shorter than this says nothing about a fold.
MIN_RESIDUES = 3


def ca_trace(
    structure: ProteinStructure, chain: str, port: str
) -> tuple[np.ndarray, str]:
    """The Cα coordinates, in ångströms, and one-letter sequence of one chain.

    Residues keep the order the structure lists them in. One with no Cα is left out,
    so a chain with gaps aligns on the residues it does have, and a non-standard one
    enters the sequence as ``X``.

    Args:
        structure: The structure to read.
        chain: The author chain id to take, or empty for its only protein chain.
        port: The input port the structure arrived on, for the error messages.

    Raises:
        ValueError: If ``chain`` is empty and the structure has more than one protein
            chain, if the named chain has no protein residues, or if fewer than
            ``MIN_RESIDUES`` residues have a Cα.
    """
    traj, chains = load_mmcif(structure.structure)
    residues = [
        (chains[r.chain.index], r) for r in traj.topology.residues if r.is_protein
    ]
    present = sorted({c for c, _ in residues})
    if not chain:
        if len(present) != 1:
            raise ValueError(
                f"Structure {structure.id} on port {port!r} has protein chains "
                f"{present}; set {'reference_chain' if port == 'reference' else 'chain'} "
                "to name the one to align."
            )
        chain = present[0]
    elif chain not in present:
        raise ValueError(
            f"Chain {chain!r} has no protein residues in structure {structure.id} on "
            f"port {port!r}; it has {present}."
        )

    atoms, sequence = [], []
    for c, residue in residues:
        if c != chain:
            continue
        ca = next((a for a in residue.atoms if a.name == "CA"), None)
        if ca is None:
            continue
        atoms.append(ca.index)
        sequence.append(residue.code or "X")
    if len(atoms) < MIN_RESIDUES:
        raise ValueError(
            f"Chain {chain!r} of structure {structure.id} on port {port!r} has "
            f"{len(atoms)} residues with a Cα, fewer than {MIN_RESIDUES}."
        )
    xyz = np.asarray(traj.xyz[0, atoms] * 10, dtype=np.float64)  # nm to Å
    return xyz, "".join(sequence)


class Tmalign(BaseNode[TmalignConfig]):
    """Superpose each structure on one reference structure with TM-align."""

    def run(
        self, structure: list[ProteinStructure], reference: list[ProteinStructure]
    ) -> list[StructureAlignment]:
        """Return one alignment per structure, each against the one reference.

        Raises:
            ValueError: If the ``reference`` port carries more than one structure.
        """
        if len(reference) != 1:
            raise ValueError(
                f"The reference port takes one structure, not {len(reference)}. "
                "Narrow it to a single structure, e.g. with top_k, so that which "
                "one every alignment is against is recorded."
            )
        (ref,) = reference
        ref_xyz, ref_seq = ca_trace(ref, self.config.reference_chain, "reference")
        return [self._align(s, ref, ref_xyz, ref_seq) for s in structure]

    def _align(
        self,
        s: ProteinStructure,
        ref: ProteinStructure,
        ref_xyz: np.ndarray,
        ref_seq: str,
    ) -> StructureAlignment:
        xyz, seq = ca_trace(s, self.config.chain, "structure")
        # The query goes first, so tm_norm_chain1 is normalised by its length.
        out = tm_align(xyz, ref_xyz, seq, ref_seq)
        pairs = [
            (a, b)
            for a, b in zip(out.seqxA, out.seqyA, strict=True)
            if a != "-" and b != "-"
        ]
        # An X stands for a residue TM-align could not name, so it matches nothing.
        same = sum(1 for a, b in pairs if a == b and a != "X")
        return StructureAlignment(
            sequence=s.sequence,
            structure_id=s.id,
            reference_id=ref.id,
            # Rounded, because an id is a hash of these: the last bits of a float
            # may differ between platforms, and would then split one result in two.
            rmsd=round(float(out.rmsd), 3),
            tm_score_query=round(float(out.tm_norm_chain1), 4),
            tm_score_reference=round(float(out.tm_norm_chain2), 4),
            aligned_length=len(pairs),
            seq_identity=round(same / len(pairs), 4) if pairs else 0.0,
        )
