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

    def fake(sequences, num_loops, num_sampling_steps, seed):
        calls.append((sequences, num_loops, num_sampling_steps, seed))
        return [f"data_{s[:4]}\n_entry.id {s[:4]}\n" for s in sequences]

    monkeypatch.setattr(function, "fold_remote", fake)
    return calls


def test_each_sequence_comes_back_as_a_structure(folded):
    node = Esmfold2Fold(Esmfold2FoldConfig())
    out = node.run(sequence=[UBIQUITIN, AminoAcidSequence(sequence="MALK")])
    assert [type(o) for o in out] == [ProteinStructure, ProteinStructure]
    assert [o.sequence for o in out] == [UBIQUITIN.sequence, "MALK"]
    assert out[0].structure.startswith("data_MQIF")
    # One call, so the weights are loaded once for the whole list.
    assert folded == [([UBIQUITIN.sequence, "MALK"], 3, 50, 0)]


def test_the_config_sets_what_the_gpu_is_asked_for(folded):
    config = Esmfold2FoldConfig(num_loops=1, num_sampling_steps=20, seed=7)
    Esmfold2Fold(config).run(sequence=[UBIQUITIN])
    assert folded == [([UBIQUITIN.sequence], 1, 20, 7)]
    # A different setting is a different node, so its results cache separately.
    assert config.config_hash != Esmfold2FoldConfig().config_hash


def test_a_trailing_stop_codon_is_not_folded(folded):
    (out,) = Esmfold2Fold(Esmfold2FoldConfig()).run(
        sequence=[AminoAcidSequence(sequence="MALK*")]
    )
    assert folded[0][0] == ["MALK"]
    assert out.sequence == "MALK"


@pytest.mark.parametrize("sequence", ["MA*LK", "***"])
def test_a_sequence_that_is_not_one_chain_is_rejected(folded, sequence):
    with pytest.raises(ValueError):
        Esmfold2Fold(Esmfold2FoldConfig()).run(
            sequence=[AminoAcidSequence(sequence=sequence)]
        )


def test_no_sequences_means_no_gpu(folded):
    assert Esmfold2Fold(Esmfold2FoldConfig()).run(sequence=[]) == []
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
