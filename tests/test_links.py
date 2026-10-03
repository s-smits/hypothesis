from temporalio import activity
from temporalio.testing import ActivityEnvironment

from node_dag import factory
from node_dag.links import Link, report
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.types import AminoAcidSequence, Dna
from temporal.dag.activities import RunNodeInput, run_tool, step_links

URL = "https://modal.com/apps/ap-eRrUbPsdYJpbLtbZJUgl3O"


class Remote:
    """A node whose work happens elsewhere, so it reports where to watch it."""

    def __init__(self, config):
        pass

    def run(self, sequence):
        """Report a link, then answer as the real node would."""
        self.workflow_id = activity.info().workflow_id
        report("Modal", URL)
        return [AminoAcidSequence(sequence="M") for _ in sequence]


def _run(step: str, monkeypatch) -> str:
    """Run a reporting node as the activity would, and return the run it ran in."""
    node = Remote(None)
    monkeypatch.setitem(factory.MAPPING, DnaToProteinConfig, lambda config: node)
    inp = RunNodeInput(
        config=DnaToProteinConfig(),
        inputs={"sequence": [Dna(sequence="ATG")]},
        step=step,
    )
    ActivityEnvironment().run(run_tool, inp)
    return node.workflow_id


def test_a_link_a_step_reports_is_there_for_the_ui(results_dir, monkeypatch):
    workflow_id = _run("fold", monkeypatch)
    assert step_links(workflow_id) == {"fold": [Link(label="Modal", url=URL)]}
    # It is written as the node reports it, not when the step ends, so it is there
    # while the work is still running.
    assert (results_dir / "links" / workflow_id / "fold.json").exists()


def test_a_run_with_no_remote_work_has_no_links(results_dir):
    assert step_links("nothing-ran") == {}


def test_reporting_outside_a_run_does_nothing(results_dir):
    report("Modal", URL)  # No activity, so nowhere to put it, and no error either.
    assert not (results_dir / "links").exists()


def test_the_step_is_not_part_of_what_a_node_is_cached_by():
    # The same work in another step, or another run, is still a cache hit.
    inp = RunNodeInput(
        config=DnaToProteinConfig(), inputs={"sequence": [Dna(sequence="ATG")]}
    )
    assert inp.cache_path() == inp.model_copy(update={"step": "fold"}).cache_path()
