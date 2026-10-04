import pytest
from pydantic import ValidationError

from node_dag.dag import Dag
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.esmfold2_fold import function
from node_dag.nodes.tools.esmfold2_fold.config import Esmfold2FoldConfig
from node_dag.nodes.tools.esmfold2_fold.function import Esmfold2Fold
from node_dag.types import AminoAcidSequence, ProteinStructure

UBIQUITIN = AminoAcidSequence(
    sequence="MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
)


@pytest.fixture
def folded(monkeypatch):
    """Answer in place of the GPU, and record what it was asked to fold."""
    calls = []

    def fake(groups, co_fold, num_loops, num_sampling_steps, seed):
        calls.append((groups, co_fold, num_loops, num_sampling_steps, seed))
        return [f"data_{g[0][:4]}\n_entry.id {g[0][:4]}\n" for g in groups]

    monkeypatch.setattr(function, "fold_remote", fake)
    return calls


def fold(config=None, sequence=(), partner=()):
    """Run the node, as a DAG does: every port, the optional one possibly empty."""
    return Esmfold2Fold(config or Esmfold2FoldConfig()).run(
        sequence=list(sequence), partner=list(partner)
    )


def test_each_sequence_comes_back_as_a_structure(folded):
    out = fold(sequence=[UBIQUITIN, AminoAcidSequence(sequence="MALK")])
    assert [type(o) for o in out] == [ProteinStructure, ProteinStructure]
    assert [o.sequence for o in out] == [UBIQUITIN.sequence, "MALK"]
    assert out[0].structure.startswith("data_MQIF")
    # One call, so the weights are loaded once for the whole list, and one chain per
    # group, so the cheaper single-sequence model folds them.
    assert folded == [([[UBIQUITIN.sequence], ["MALK"]], False, 3, 50, 0)]


def test_the_config_sets_what_the_gpu_is_asked_for(folded):
    config = Esmfold2FoldConfig(num_loops=1, num_sampling_steps=20, seed=7)
    fold(config, sequence=[UBIQUITIN])
    assert folded == [([[UBIQUITIN.sequence]], False, 1, 20, 7)]
    # A different setting is a different node, so its results cache separately.
    assert config.config_hash != Esmfold2FoldConfig().config_hash


def test_as_complex_folds_every_sequence_into_one_structure(folded):
    config = Esmfold2FoldConfig(as_complex=True)
    (out,) = fold(config, sequence=[UBIQUITIN, AminoAcidSequence(sequence="MALK*")])
    # The whole list went in one fold, so the chains sit in one structure.
    assert folded == [([[UBIQUITIN.sequence, "MALK"]], True, 3, 50, 0)]
    # The chains are concatenated in arrival order, as sequence_of keeps them.
    assert out.sequence == UBIQUITIN.sequence + "MALK"
    assert out.structure.startswith("data_MQIF")
    # A complex fold caches separately from the same sequences folded alone.
    assert config.config_hash != Esmfold2FoldConfig().config_hash


def test_a_trailing_stop_codon_is_not_folded(folded):
    (out,) = fold(sequence=[AminoAcidSequence(sequence="MALK*")])
    assert folded[0][0] == [["MALK"]]
    assert out.sequence == "MALK"


@pytest.mark.parametrize("sequence", ["MA*LK", "***"])
def test_a_sequence_that_is_not_one_chain_is_rejected(folded, sequence):
    with pytest.raises(ValueError):
        fold(sequence=[AminoAcidSequence(sequence=sequence)])


def test_no_sequences_means_no_gpu(folded):
    assert fold(sequence=[]) == []
    assert fold(sequence=[], partner=[UBIQUITIN]) == []
    assert folded == []


def test_a_dag_can_fold_what_it_translated():
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
        },
    )


class FakeApp:
    """A modal.App: a wrapper that keeps the object the backend talked to."""

    def __init__(self, inner):
        self.inner = inner


def test_the_link_is_the_page_the_app_is_actually_on():
    # /apps/<app id> 404s: the page is under the workspace and environment too, which
    # only the running app knows.
    url = "https://modal.com/apps/someone/main/ap-123"
    running = type("Running", (), {"app_page_url": url})()
    inner = type("Inner", (), {"_running_app": running})()
    assert function.app_page_url(FakeApp(inner)) == url


def test_a_modal_that_keeps_the_page_elsewhere_gives_no_link():
    # Better no link than one that goes nowhere.
    assert function.app_page_url(FakeApp(object())) is None


def test_a_fold_may_take_longer_than_a_local_step():
    # A cold start builds the image and pulls the weights, which the default five
    # minutes does not cover, so the run would give up before the GPU folded anything.
    assert Esmfold2FoldConfig.timeout_minutes > DnaToProteinConfig.timeout_minutes


@pytest.mark.parametrize("kwargs", [{"num_loops": 0}, {"num_sampling_steps": 0}])
def test_a_run_that_would_predict_nothing_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        Esmfold2FoldConfig(**kwargs)


def test_a_partner_is_folded_with_every_sequence(folded):
    target = AminoAcidSequence(sequence="MKWVTF")
    designs = [UBIQUITIN, AminoAcidSequence(sequence="MALK")]
    out = fold(sequence=designs, partner=[target])
    # One group per design, each with the target beside it, in one call.
    assert folded == [
        (
            [[UBIQUITIN.sequence, "MKWVTF"], ["MALK", "MKWVTF"]],
            True,  # Chains conditioned on each other need the full model.
            3,
            50,
            0,
        )
    ]
    # One complex per design, its sequence the chains in arrival order.
    assert [o.sequence for o in out] == [UBIQUITIN.sequence + "MKWVTF", "MALKMKWVTF"]


def test_as_complex_folds_both_ports_into_one_structure(folded):
    (out,) = fold(
        Esmfold2FoldConfig(as_complex=True),
        sequence=[AminoAcidSequence(sequence="MALK")],
        partner=[AminoAcidSequence(sequence="MKWVTF")],
    )
    assert folded == [([["MALK", "MKWVTF"]], True, 3, 50, 0)]
    assert out.sequence == "MALKMKWVTF"


def test_a_homodimer_is_asked_for_with_both_ports(folded):
    # Identical entities merge within a port, so the same sequence twice on one port
    # is one chain. One on each port stays two.
    (out,) = fold(
        sequence=[AminoAcidSequence(sequence="MALK")],
        partner=[AminoAcidSequence(sequence="MALK")],
    )
    assert folded[0][0] == [["MALK", "MALK"]]
    assert out.sequence == "MALKMALK"


def test_an_unwired_partner_folds_exactly_as_before(folded):
    # The port was added without changing what a plan that ignores it does, down to
    # the model chosen and the config hash a saved plan carries.
    assert fold(sequence=[UBIQUITIN])[0].sequence == UBIQUITIN.sequence
    assert folded == [([[UBIQUITIN.sequence]], False, 3, 50, 0)]
    assert Esmfold2FoldConfig().config_hash == "96e28a41"


def test_a_dag_may_wire_the_partner_or_leave_it_out():
    for inputs in ({"sequence": "designs"}, {"sequence": "designs", "partner": "tgt"}):
        Dag(
            inputs={"designs": "amino_acid_sequence", "tgt": "amino_acid_sequence"},
            steps={
                "structure": {"config": {"name": "esmfold2_fold"}, "inputs": inputs}
            },
        )


def test_a_dag_still_rejects_a_port_the_node_does_not_have():
    with pytest.raises(ValidationError, match=r"\['partner'\] may be left out"):
        Dag(
            inputs={"designs": "amino_acid_sequence"},
            steps={
                "structure": {
                    "config": {"name": "esmfold2_fold"},
                    "inputs": {"sequence": "designs", "ligand": "designs"},
                }
            },
        )
