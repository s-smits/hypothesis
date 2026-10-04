import hashlib
import json

import pytest

from node_dag import factory
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.nodes.filters.beats_reference.config import BeatsReferenceConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.esmfold2_fold.config import Esmfold2FoldConfig
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.types import AminoAcidSequence, Dna
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
    seqs = [Dna(sequence="ATGGCTCTGAAATAA"), Dna(sequence="ATGGCCCTGAAATAA")]
    inp = RunNodeInput(
        config=OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA"),
        inputs={"sequence": seqs},
    )
    rows = run_score(inp)
    assert "expression" in rows[0]
    monkeypatch.setitem(factory.MAPPING, OstirExpressionConfig, Broken)
    assert run_score(inp) == rows
    # Another reference is another score, so it is not read from the cache.
    other = inp.model_copy(
        update={"config": OstirExpressionConfig(utr="TTCTAGACCTCCTTATAAAAA")}
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


def _beats(reference: float | None) -> RunNodeInput:
    return RunNodeInput(
        config=BeatsReferenceConfig(
            column="c", reference=Dna(sequence="ATG"), scored_in="base"
        ),
        inputs={"items": [Dna(sequence="ATG"), Dna(sequence="AAA")]},
        values=[3.0, 12.0],
        reference=reference,
    )


def test_a_filter_with_a_reference_caches_per_reference_score(results_dir, monkeypatch):
    inp = _beats(10.0)
    assert run_filter(inp) == [False, True]
    monkeypatch.setitem(factory.MAPPING, BeatsReferenceConfig, Broken)
    assert run_filter(inp) == [False, True]  # Loaded, not run.
    # Another reference score is another answer, so it is not read from the cache.
    assert _beats(2.0).cache_path() != inp.cache_path()
    with pytest.raises(RuntimeError):
        run_filter(_beats(2.0))


def test_a_filter_without_a_reference_keeps_the_key_it_had_before_references():
    inp = _beats(None)
    key = {
        **inp.model_dump(mode="json", exclude={"step", "reference"}),
        "version": inp.config.version,
    }
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    assert inp.cache_path().name == f"{digest}.json"


def test_a_filter_without_a_reference_keeps_its_key_and_hash_from_before_references():
    # Both literals were taken from the code before beats_reference: a change that moves
    # either one orphans every saved at_least result.
    inp = RunNodeInput(
        config=AtLeastConfig(column="c", threshold=10),
        inputs={"items": [Dna(sequence="ATG"), Dna(sequence="AAA")]},
        values=[3.0, 12.0],
    )
    assert inp.config.config_hash == "b79fb657"
    assert inp.cache_path().name == (
        "7c9e2944895710a84844c5652155609bdd72b9a17244962ec1374358070d3ec9.json"
    )


def test_an_unwired_optional_port_keeps_the_key_the_node_had_without_it():
    # The literal is the key from before esmfold2_fold had a partner port: a change
    # that moves it re-folds, on a GPU, everything already cached.
    sequence = [AminoAcidSequence(sequence="MALK")]
    without = RunNodeInput(config=Esmfold2FoldConfig(), inputs={"sequence": sequence})
    unwired = RunNodeInput(
        config=Esmfold2FoldConfig(), inputs={"sequence": sequence, "partner": []}
    )
    assert without.cache_path() == unwired.cache_path()
    assert without.cache_path().name == (
        "492a296be81ffde8eb8d31dc98f6b64cd8ee7098fb2769a953b2c89db9c14687.json"
    )


def test_a_wired_partner_caches_apart_from_an_unwired_one():
    sequence = [AminoAcidSequence(sequence="MALK")]
    unwired = RunNodeInput(
        config=Esmfold2FoldConfig(), inputs={"sequence": sequence, "partner": []}
    )
    wired = RunNodeInput(
        config=Esmfold2FoldConfig(),
        inputs={
            "sequence": sequence,
            "partner": [AminoAcidSequence(sequence="MKWVTF")],
        },
    )
    assert unwired.cache_path() != wired.cache_path()
