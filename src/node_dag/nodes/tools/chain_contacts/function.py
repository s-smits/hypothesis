import io

import mdtraj as md
import numpy as np
from mdtraj.formats.pdbx.pdbxfile import PDBxFile
from mdtraj.formats.pdbx.PdbxReader import PdbxReader

from node_dag.nodes.base import BaseNode
from node_dag.nodes.tools.chain_contacts.config import ChainContactsConfig
from node_dag.types import ProteinContacts, ProteinStructure, ResidueContact


def _author_chains(structure: str) -> dict[str, str]:
    """Maps each atom's serial to its author chain id."""
    data: list = []
    PdbxReader(io.StringIO(structure)).read(data)
    atoms = data[0].getObj("atom_site")
    serial = atoms.getAttributeIndex("id")
    chain = atoms.getAttributeIndex("auth_asym_id")
    if chain == -1:
        chain = atoms.getAttributeIndex("label_asym_id")
    return {r[serial]: r[chain] for r in atoms.getRowList()}


def load_mmcif(structure: str) -> tuple[md.Trajectory, list[str]]:
    """The structure as an mdtraj trajectory, and each mdtraj chain's author chain id.

    mdtraj names chains by ``label_asym_id`` whenever that has more distinct values
    than ``auth_asym_id``, as it does in most deposited entries, while it numbers
    residues by ``auth_seq_id``. Take the author chain id from each chain's first
    atom instead, so a residue's chain, number and insertion code all come from the
    author columns. mdtraj starts a new chain whenever either id changes, so every
    atom in an mdtraj chain shares one author chain id.

    Raises:
        ValueError: If the structure has more than one model.
    """
    pdbx = PDBxFile(io.StringIO(structure))
    if (n := pdbx.getNumFrames()) != 1:
        raise ValueError(f"Expected one model, found {n}: pick one first.")
    xyz = np.asarray(pdbx.getPositions(asNumpy=True, frame=0), dtype=np.float32)
    traj = md.Trajectory(xyz[None], pdbx.topology)
    auth = _author_chains(structure)
    chains = [auth[str(next(c.atoms).serial)] for c in traj.topology.chains]
    return traj, chains


def residue_label(residue: md.core.topology.Residue, chain: str) -> str:
    """``chain:res_name:res_num:insertion_code``.

    mdtraj's mmCIF reader passes the insertion code where ``add_residue`` takes a
    segment id, so for mmCIF input ``segment_id`` holds it.
    """
    return f"{chain}:{residue.name}:{residue.resSeq}:{residue.segment_id}"


class ChainContacts(BaseNode[ChainContactsConfig]):
    """Find residue contacts between the protein chains of each structure."""

    def run(self, structure: list[ProteinStructure]) -> list[ProteinContacts]:
        """Return one set of inter-chain contacts per structure."""
        return [self._contacts(s) for s in structure]

    def _sides(self, s: ProteinStructure, present: set[str]) -> tuple[set, set]:
        """The chains on each side of the interface, or two empty sets for all."""
        a, b = set(self.config.chains_a), set(self.config.chains_b)
        if missing := (a | b) - present:
            raise ValueError(
                f"Chains {sorted(missing)} have no protein residues in structure "
                f"{s.id}; it has {sorted(present)}."
            )
        if not a:
            return set(), set()
        return a, b or present - a

    def _contacts(self, s: ProteinStructure) -> ProteinContacts:
        traj, chains = load_mmcif(s.structure)
        top = traj.topology
        chain_of = {r.index: chains[r.chain.index] for r in top.residues}
        protein = {r.index for r in top.residues if r.is_protein}
        side_a, side_b = self._sides(s, {chain_of[i] for i in protein})

        def oriented(i: int, j: int) -> tuple[int, int] | None:
            # A pair on two allowed sides, with the chains_a residue first.
            ci, cj = chain_of[i], chain_of[j]
            if ci == cj:
                return None
            if not side_a:
                return (i, j) if (ci, i) < (cj, j) else (j, i)
            if ci in side_a and cj in side_b:
                return i, j
            if cj in side_a and ci in side_b:
                return j, i
            return None

        candidates = {
            p
            for i, j in self._near_pairs(traj, protein)
            if (p := oriented(i, j)) is not None
        }
        contacts = []
        if candidates:
            distances, pairs = md.compute_contacts(
                traj,
                np.array(sorted(candidates)),
                scheme=self.config.scheme,
                periodic=False,  # A crystal's unit cell would add symmetry mates.
            )
            for (i, j), d in zip(pairs, distances[0] * 10, strict=True):  # nm to Å
                if d <= self.config.cutoff:
                    contacts.append(
                        ResidueContact(
                            residue_a=residue_label(top.residue(i), chain_of[i]),
                            residue_b=residue_label(top.residue(j), chain_of[j]),
                            distance=round(float(d), 3),
                        )
                    )
        contacts.sort(key=lambda c: (c.distance, c.residue_a, c.residue_b))
        return ProteinContacts(
            sequence=s.sequence, structure_id=s.id, contacts=contacts
        )

    def _near_pairs(
        self, traj: md.Trajectory, protein: set[int]
    ) -> set[tuple[int, int]]:
        """Protein residue pairs with any two of their atoms within the cutoff.

        Every scheme measures a minimum over some of a pair's atoms, so it can never
        be shorter than the minimum over all of them. Checking all atoms first
        therefore loses no contact, and spares measuring every pair of residues.
        """
        res = np.array([a.residue.index for a in traj.topology.atoms])
        neighbours = md.compute_neighborlist(
            traj, self.config.cutoff / 10, periodic=False
        )
        return {
            (int(res[a]), int(res[b]))
            for a, near in enumerate(neighbours)
            if res[a] in protein
            for b in near
            if res[b] in protein and res[a] != res[b]
        }
