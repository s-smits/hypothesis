import pytest

from node_dag import factory
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.types import AminoAcidSequence, Dna, Score
from temporal.dag.activities import RunNodeInput, run_filter, run_score, run_tool


class Broken:
    """A node that fails if it runs, so a pass proves the cache was read."""

    def __init__(self, config):
        pass

    def run(self, **inputs):
        """Fail."""
        raise RuntimeError("ran instead of loading the cache")


def test_a_node_runs_once_per_config_and_inputs(results_dir, monkeypatch):
    inp = RunNodeInput(
        config=DnaToProteinConfig(),
        inputs={"sequence": [Dna(sequence="ATGATGATG"), Dna(sequence="AAATTTGGG")]},
    )
    result = run_tool(inp)
    assert result == [
        AminoAcidSequence(sequence="MMM"),
        AminoAcidSequence(sequence="KFG"),
    ]
    (path,) = (results_dir / "nodes" / "dna_to_protein").iterdir()
    assert path == inp.cache_path()

    monkeypatch.setitem(factory.MAPPING, DnaToProteinConfig, Broken)
    assert run_tool(inp) == result  # Loaded, not run.
    # One more entity in the list is a different run.
    more = inp.model_copy(
        update={"inputs": {"sequence": [*inp.inputs["sequence"], Dna(sequence="ATA")]}}
    )
    with pytest.raises(RuntimeError):
        run_tool(more)

    monkeypatch.setattr(DnaToProteinConfig, "version", 2)
    with pytest.raises(RuntimeError):
        run_tool(inp)


def test_a_scoring_node_caches_per_config(results_dir, monkeypatch):
    seqs = [Dna(sequence="ATGGCT"), Dna(sequence="ATGGCC")]
    inp = RunNodeInput(
        config=DnaAtomScoreConfig(reference=seqs[0]), inputs={"sequence": seqs}
    )
    rows = run_score(inp)
    assert rows[0]["amino_acid_changes"] == Score(value=0)
    monkeypatch.setitem(factory.MAPPING, DnaAtomScoreConfig, Broken)
    assert run_score(inp) == rows
    # Another reference is another score, so it is not read from the cache.
    other = inp.model_copy(
        update={"config": DnaAtomScoreConfig(reference=Dna(sequence="ATGAAA"))}
    )
    with pytest.raises(RuntimeError):
        run_score(other)


def test_a_filter_caches_what_it_keeps(results_dir, monkeypatch):
    seqs = [Dna(sequence="ATG"), Dna(sequence="AAA")]
    inp = RunNodeInput(
        config=AtLeastConfig(column="c", threshold=10),
        inputs={"items": seqs},
        values=[3.0, 12.0],
    )
    assert run_filter(inp) == [False, True]
    monkeypatch.setitem(factory.MAPPING, AtLeastConfig, Broken)
    assert run_filter(inp) == [False, True]
    assert inp.cache_path().parent == results_dir / "nodes" / "at_least"
    # The scores are part of the key.
    with pytest.raises(RuntimeError):
        run_filter(inp.model_copy(update={"values": [30.0, 12.0]}))
