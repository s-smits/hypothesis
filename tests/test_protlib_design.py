import pytest
from pydantic import ValidationError

from node_dag.dag import Dag
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.protlib_design import function
from node_dag.nodes.tools.protlib_design.config import ProtlibDesignConfig
from node_dag.nodes.tools.protlib_design.function import (
    ProtlibDesign,
    apply_solution,
    positions_for,
)
from node_dag.types import AminoAcidSequence, ProteinStructure

STRUCTURE = ProteinStructure(sequence="MALK", structure="data_ mmcif\n_entry.id X\n")


@pytest.fixture
def designed(monkeypatch):
    """Answer in place of the GPU, and record what it was asked to design."""
    calls = []

    def fake(payloads, options):
        calls.append((payloads, options))
        return [["MA1G,AA2V"] for _ in payloads]

    monkeypatch.setattr(function, "design_remote", fake)
    return calls


def _config(**kwargs):
    return ProtlibDesignConfig.model_validate({"positions": ["*A*"], **kwargs})


def test_each_structure_comes_back_as_a_library(designed):
    node = ProtlibDesign(_config())
    out = node.run(structure=[STRUCTURE])
    (payloads, options) = designed[0]
    assert payloads == [
        {
            "sequence": "MALK",
            "mmcif": STRUCTURE.structure,
            "positions": ["MA1", "AA2", "LA3", "KA4"],
        }
    ]
    assert options["library_size"] == 10
    assert out == [AminoAcidSequence(sequence="GVLK")]


def test_the_config_sets_what_the_gpu_is_asked_for(designed):
    config = _config(
        positions=["*A{2-3}", "KA4"],
        library_size=3,
        min_mut=2,
        max_mut=2,
        plm_models=["Rostlab/prot_bert"],
        forbidden_aa=["C"],
        schedule=2,
        schedule_param=[1, 4],
        seed=7,
    )
    ProtlibDesign(config).run(structure=[STRUCTURE])
    payloads, options = designed[0]
    assert payloads[0]["positions"] == ["AA2", "LA3", "KA4"]
    assert options["library_size"] == 3
    assert options["min_mut"] == options["max_mut"] == 2
    assert options["plm_models"] == ["Rostlab/prot_bert"]
    assert options["forbidden_aa"] == ["C"]
    assert options["schedule"] == 2
    assert options["schedule_param"] == [1, 4]
    assert options["seed"] == 7
    # A different setting is a different node, so its results cache separately.
    assert config.config_hash != _config().config_hash


def test_every_position_spec_form_expands():
    sequence = "MALK"
    assert positions_for("*A*", sequence) == ["MA1", "AA2", "LA3", "KA4"]
    assert positions_for("*A{2-3}", sequence) == ["AA2", "LA3"]
    assert positions_for("LA3", sequence) == ["LA3"]


@pytest.mark.parametrize("spec", ["ZA1", "LA5", "*A{3-2}", "*A{0-2}", "A12", "LA-3"])
def test_a_spec_that_names_no_residue_is_rejected(spec):
    with pytest.raises(ValueError):
        positions_for(spec, "MALK")


def test_positions_may_not_span_two_chains(designed):
    with pytest.raises(ValueError, match="one chain"):
        ProtlibDesign(_config(positions=["MA1", "KB4"])).run(structure=[STRUCTURE])


def test_a_sequence_with_a_stop_codon_is_rejected(designed):
    with pytest.raises(ValueError, match="stop codon"):
        ProtlibDesign(_config()).run(
            structure=[ProteinStructure(sequence="MA*K", structure="x")]
        )


def test_no_structures_means_no_gpu(designed):
    assert ProtlibDesign(_config()).run(structure=[]) == []
    assert designed == []


def test_a_solution_is_applied_to_the_wildtype():
    assert apply_solution("MALK", "MA1G,AA2V") == "GVLK"
    assert apply_solution("MALK", "KA4R") == "MALR"


def test_a_solution_off_another_sequence_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        apply_solution("MALK", "GA1C")


def test_a_dag_can_design_what_it_folded():
    Dag(
        inputs={"seqs": "dna"},
        steps={
            "protein": {
                "config": {"name": "dna_to_protein"},
                "inputs": {"sequence": "seqs"},
            },
            "structure": {
                "config": {"name": "esmfold2_fold"},
                "inputs": {"sequence": "protein"},
            },
            "library": {
                "config": {"name": "protlib_design", "positions": ["*A*"]},
                "inputs": {"structure": "structure"},
            },
        },
    )


def test_a_design_may_take_longer_than_a_local_step():
    # A cold start builds the image and pulls the weights, which the default five
    # minutes does not cover, so the run would give up before the GPU scored a
    # single mutation.
    assert ProtlibDesignConfig.timeout_minutes > DnaToProteinConfig.timeout_minutes


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_mut": 3, "max_mut": 2},
        {"use_ifold": False, "plm_models": []},
        {"schedule": 1},
        {"forbidden_aa": ["Z"]},
        {"positions": []},
        {"library_size": 0},
    ],
)
def test_a_design_that_cannot_run_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        _config(**kwargs)
