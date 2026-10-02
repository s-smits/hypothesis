import pytest

from node_dag import factory
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.nodes.tools.add.config import AddConfig
from node_dag.types import FooBar
from temporal.dag.activities import RunNodeInput, run_decision, run_tool


class Broken:
    """A node that fails if it runs, so a pass proves the cache was read."""

    def __init__(self, config):
        pass

    def run(self, **inputs):
        """Fail."""
        raise RuntimeError("ran instead of loading the cache")


def test_a_node_runs_once_per_config_and_inputs(results_dir, monkeypatch):
    inp = RunNodeInput(config=AddConfig(amount=1), inputs={"value": FooBar(count=1)})
    assert run_tool(inp) == FooBar(count=2)
    (path,) = (results_dir / "nodes" / "add").iterdir()
    assert path == inp.cache_path()

    monkeypatch.setitem(factory.MAPPING, AddConfig, Broken)
    assert run_tool(inp) == FooBar(count=2)  # Loaded, not run.
    for changed in (
        inp.model_copy(update={"inputs": {"value": FooBar(count=5)}}),
        inp.model_copy(update={"config": AddConfig(amount=2)}),
    ):
        with pytest.raises(RuntimeError):
            run_tool(changed)

    monkeypatch.setattr(AddConfig, "version", 2)
    with pytest.raises(RuntimeError):
        run_tool(inp)


def test_a_decision_caches_its_branch(results_dir, monkeypatch):
    inp = RunNodeInput(
        config=AtLeastConfig(threshold=10), inputs={"value": FooBar(count=3)}
    )
    assert run_decision(inp) is False
    monkeypatch.setitem(factory.MAPPING, AtLeastConfig, Broken)
    assert run_decision(inp) is False
    assert inp.cache_path().parent == results_dir / "nodes" / "at_least"
