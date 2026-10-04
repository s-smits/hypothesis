import numpy as np
import pytest

from node_dag.dag import Dag
from node_dag.nodes.tools.tmalign.config import TmalignConfig
from node_dag.nodes.tools.tmalign.function import Tmalign
from node_dag.types import ProteinStructure, StructureAlignment, Table

THREE = {
    "A": "ALA",
    "C": "CYS",
    "D": "ASP",
    "E": "GLU",
    "F": "PHE",
    "G": "GLY",
    "H": "HIS",
    "I": "ILE",
    "K": "LYS",
    "L": "LEU",
    "M": "MET",
}
FIELDS = (
    "group_PDB",
    "id",
    "type_symbol",
    "label_atom_id",
    "label_alt_id",
    "label_comp_id",
    "label_asym_id",
    "label_entity_id",
    "label_seq_id",
    "pdbx_PDB_ins_code",
    "Cartn_x",
    "Cartn_y",
    "Cartn_z",
    "occupancy",
    "B_iso_or_equiv",
    "auth_seq_id",
    "auth_comp_id",
    "auth_asym_id",
    "auth_atom_id",
    "pdbx_PDB_model_num",
)


def mmcif(chains: dict[str, tuple[str, np.ndarray]]) -> str:
    """A Cα-only mmCIF of ``{author chain id: (sequence, coordinates in Å)}``."""
    lines = ["data_test", "#", "loop_", *[f"_atom_site.{f}" for f in FIELDS]]
    serial = 0
    for entity, (cid, (seq, xyz)) in enumerate(chains.items(), start=1):
        for i, (aa, (x, y, z)) in enumerate(zip(seq, xyz, strict=True), start=1):
            serial += 1
            comp = THREE[aa]
            lines.append(
                f"ATOM {serial} C CA . {comp} {cid} {entity} {i} ? "
                f"{x:.3f} {y:.3f} {z:.3f} 1.00 0.00 {i} {comp} {cid} CA 1"
            )
    return "\n".join(lines) + "\n#\n"


def helix(n: int, offset: int = 0) -> np.ndarray:
    """An ideal α-helical Cα trace of ``n`` residues, starting ``offset`` along it."""
    i = np.arange(offset, offset + n)
    return np.stack([2.3 * np.cos(1.745 * i), 2.3 * np.sin(1.745 * i), 1.5 * i], axis=1)


def structure(seq: str, xyz: np.ndarray, chain: str = "A") -> ProteinStructure:
    return ProteinStructure(sequence=seq, structure=mmcif({chain: (seq, xyz)}))


# A 60-residue helix and the same helix drawn out to 100 residues: different lengths
# and different sequences, which is what TM-align is for.
SHORT = structure("ACDEFGHIKL" * 6, helix(60))
LONG = structure("MLKIHGFEDCA" * 9 + "M", helix(100))


def test_structures_of_different_length_and_sequence_align():
    (out,) = Tmalign(TmalignConfig()).run(structure=[SHORT], reference=[LONG])
    assert isinstance(out, StructureAlignment)
    assert out.structure_id == SHORT.id
    assert out.reference_id == LONG.id
    # The short helix is a fragment of the long one, so every one of its residues
    # aligns and the superposition is near exact.
    assert out.aligned_length == 60
    assert out.rmsd < 0.01
    # The TM-score is 1 over the query's 60 residues and 0.6 over the reference's 100.
    assert out.tm_score_query == pytest.approx(1.0, abs=1e-3)
    assert out.tm_score_reference == pytest.approx(0.6, abs=1e-3)
    # The sequences disagree at all but the residues their repeats happen to share.
    assert 0.0 < out.seq_identity < 0.3
    assert out.sequence == SHORT.sequence


def test_one_alignment_comes_back_per_structure_in_order():
    designs = [SHORT, structure("ACDEFGHIKL" * 4, helix(40))]
    out = Tmalign(TmalignConfig()).run(structure=designs, reference=[LONG])
    assert [o.structure_id for o in out] == [d.id for d in designs]
    assert {o.reference_id for o in out} == {LONG.id}
    assert [o.aligned_length for o in out] == [60, 40]


def test_a_dissimilar_fold_scores_far_below_a_matching_one():
    rng = np.random.default_rng(0)
    coil = structure("ACDEFGHIKL" * 6, rng.normal(0, 12, (60, 3)))
    matching, scrambled = Tmalign(TmalignConfig()).run(
        structure=[SHORT, coil], reference=[LONG]
    )
    assert matching.tm_score_query > 0.9
    assert scrambled.tm_score_query < 0.3
    assert scrambled.rmsd > matching.rmsd


def test_the_same_pair_measures_the_same_every_time():
    config = TmalignConfig()
    (first,) = Tmalign(config).run(structure=[SHORT], reference=[LONG])
    (again,) = Tmalign(config).run(structure=[SHORT], reference=[LONG])
    assert first == again
    assert first.id == again.id


def test_a_reference_port_without_exactly_one_structure_is_rejected():
    node = Tmalign(TmalignConfig())
    with pytest.raises(ValueError, match="takes one structure, not 2"):
        node.run(structure=[SHORT], reference=[LONG, SHORT])
    with pytest.raises(ValueError, match="takes one structure, not 0"):
        node.run(structure=[SHORT], reference=[])


def test_a_chain_has_to_be_named_when_a_structure_has_several():
    pair = ProteinStructure(
        sequence="ACDEFGHIKL" * 6 + "ACDEFGHIKL" * 4,
        structure=mmcif(
            {
                "H": ("ACDEFGHIKL" * 6, helix(60)),
                "L": ("ACDEFGHIKL" * 4, helix(40) + 40.0),
            }
        ),
    )
    node = Tmalign(TmalignConfig())
    with pytest.raises(ValueError, match=r"protein chains \['H', 'L'\]; set chain"):
        node.run(structure=[pair], reference=[LONG])
    with pytest.raises(
        ValueError, match=r"protein chains \['H', 'L'\]; set reference_chain"
    ):
        node.run(structure=[SHORT], reference=[pair])

    # Naming one picks it out, and each chain gives its own alignment.
    (first,) = Tmalign(TmalignConfig(chain="H")).run(structure=[pair], reference=[LONG])
    (second,) = Tmalign(TmalignConfig(chain="L")).run(
        structure=[pair], reference=[LONG]
    )
    assert (first.aligned_length, second.aligned_length) == (60, 40)
    # Same pair of structures, so only the measurements keep the two results apart.
    assert first.structure_id == second.structure_id
    assert first.id != second.id
    assert Table.of([first, second]).items == [first, second]


def test_a_chain_the_structure_does_not_have_is_rejected():
    node = Tmalign(TmalignConfig(chain="B"))
    with pytest.raises(ValueError, match=r"Chain 'B' has no protein residues"):
        node.run(structure=[SHORT], reference=[LONG])


def test_a_trace_too_short_to_superpose_is_rejected():
    stub = structure("AC", helix(2))
    with pytest.raises(ValueError, match="2 residues with a Cα, fewer than 3"):
        Tmalign(TmalignConfig()).run(structure=[stub], reference=[LONG])


def test_a_different_chain_is_a_different_node():
    assert TmalignConfig().config_hash != TmalignConfig(chain="H").config_hash
    assert (
        TmalignConfig(chain="H").config_hash
        != TmalignConfig(reference_chain="H").config_hash
    )


def test_a_dag_wires_both_ports():
    dag = Dag.model_validate(
        {
            "inputs": {
                "designs": "protein_structure",
                "target": "protein_structure",
            },
            "steps": {
                "compared": {
                    "config": {"name": "tmalign"},
                    "inputs": {"structure": "designs", "reference": "target"},
                }
            },
        }
    )
    assert dag.steps["compared"].deps() == {"designs", "target"}
    assert TmalignConfig.outputs() == {"<step>": StructureAlignment}


def test_a_dag_rejects_a_reference_that_is_not_a_structure():
    with pytest.raises(ValueError, match="port 'reference' takes ProteinStructure"):
        Dag.model_validate(
            {
                "inputs": {"designs": "protein_structure", "target": "dna"},
                "steps": {
                    "compared": {
                        "config": {"name": "tmalign"},
                        "inputs": {"structure": "designs", "reference": "target"},
                    }
                },
            }
        )
