import pytest

from node_dag.nodes.base import BaseFilterConfig
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.registry import Registry
from node_dag.types import Dna

REF = Dna(sequence="ATGGCTCTGAAATAA")
SCORE = DnaAtomScoreConfig(reference=REF)


def test_registering_a_node_keeps_it_and_its_first_description(results_dir):
    registry = Registry(results_dir / "registry")
    node, new = registry.register(SCORE, "atoms and protein changes")
    assert new
    assert node.id == f"dna_atom_score__{SCORE.config_hash}"
    again, new = registry.register(SCORE, "another description")
    assert not new
    assert again.description == "atoms and protein changes"
    # A new registry on the same directory sees it, so nodes last across hypotheses.
    assert Registry(results_dir / "registry").get(node.id) == node
    assert [n.id for n in Registry(results_dir / "registry").all()] == [node.id]


def test_the_summary_shows_ports_outputs_and_full_score_column_names(results_dir):
    node, _ = Registry(results_dir).register(SCORE, "score")
    summary = node.summary()
    assert summary["input"] == {"sequence": "dna"}
    assert summary["outputs"] == {"<step>": "dna"}
    assert summary["score_columns"] == {
        "atom_count": f"dna_atom_score__{SCORE.config_hash}__atom_count",
        "amino_acid_changes": f"dna_atom_score__{SCORE.config_hash}__amino_acid_changes",
    }
    assert summary["config"]["config_hash"] == SCORE.config_hash
    assert "filters_on" not in summary


def test_a_filter_summary_shows_its_column_and_branches(results_dir):
    registry = Registry(results_dir)
    column = SCORE.columns()["atom_count"]
    registry.register(SCORE, "score")
    node, _ = registry.register(AtMostConfig(column=column, threshold=400), "small")
    summary = node.summary()
    assert summary["filters_on"] == column
    assert summary["input"] == {"items": "entity"}
    assert summary["outputs"] == {"<step>.yes": "entity", "<step>.no": "entity"}
    assert isinstance(node.config, BaseFilterConfig)


def test_a_filter_needs_a_registered_scorer_of_its_column(results_dir):
    registry = Registry(results_dir)
    column = SCORE.columns()["atom_count"]
    with pytest.raises(ValueError, match="Register the scorer first"):
        registry.register(AtMostConfig(column=column, threshold=400), "small")
    registry.register(SCORE, "score")
    registry.register(AtMostConfig(column=column, threshold=400), "small")
    # A column of another config is not the same column.
    other = DnaAtomScoreConfig(reference=Dna(sequence="ATGAAA")).columns()["atom_count"]
    with pytest.raises(ValueError, match=column):
        registry.register(AtMostConfig(column=other, threshold=400), "small")


def test_score_columns_are_those_of_registered_scorers(results_dir):
    registry = Registry(results_dir)
    assert registry.score_columns() == set()
    registry.register(DnaToProteinConfig(), "translate")
    assert registry.score_columns() == set()
    registry.register(SCORE, "score")
    assert registry.score_columns() == set(SCORE.columns().values())
