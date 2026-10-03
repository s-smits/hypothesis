import asyncio
import inspect
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from temporalio import activity
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.dag import Dag, DagInput, DagProgress
from node_dag.factory import MAPPING
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.types import AminoAcidSequence, Dna, Value
from temporal.dag.activities import RunNodeInput, run_decision, run_tool, save_workflow
from temporal.dag.workflow import DagWorkflow

# Convert DNA sequence to protein, handling two paths
DAG: dict = {
    "inputs": {"seq": "dna"},
    "steps": {
        "protein": {
            "config": {"name": "dna_to_protein"},
            "inputs": {"sequence": "seq"},
        },
    },
}


@pytest.mark.parametrize(
    ("seq", "key", "expected_protein"),
    [
        ("ATGATGATG", "protein", "MMM"),
        ("AAATTTGGG", "protein", "KFG"),
    ],
)
async def test_workflow_runs_steps(seq, key, expected_protein, results_dir):
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue="t",
                workflows=[DagWorkflow],
                activities=[run_tool, run_decision, save_workflow],
                activity_executor=pool,
            ):
                out = await env.client.execute_workflow(
                    DagWorkflow.run,
                    DagInput(
                        dag=Dag.model_validate(DAG),
                        inputs={"seq": Dna(sequence=seq)},
                    ),
                    id="run-1",
                    task_queue="t",
                )
    assert out.values[key] == AminoAcidSequence(sequence=expected_protein)
    assert out.skipped == []
    saved = DagProgress.model_validate_json(
        (results_dir / "workflows" / "run-1.json").read_text()
    )
    assert saved.values == out.values
    assert sorted(k for k, s in saved.steps.items() if s == "skipped") == []


def _step(name: str, inputs: dict, **config) -> dict:
    return {"config": {"name": name, **config}, "inputs": inputs}


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"protein": _step("dna_to_protein", {"sequence": "nope"})}, "Unknown source"),
        ({"protein": _step("dna_to_protein", {"sequence": "protein"})}, "Cycle"),
    ],
)
def test_dag_rejects_bad_graphs(patch, match):
    with pytest.raises(ValidationError, match=match):
        Dag.model_validate({**DAG, "steps": {**DAG["steps"], **patch}})


def test_dag_input_must_match_declared_types():
    with pytest.raises(ValidationError, match="DAG wants inputs"):
        DagInput(
            dag=Dag.model_validate(DAG),
            inputs={"seq": AminoAcidSequence(sequence="MMM")},
        )


@pytest.mark.parametrize("config", MAPPING)
def test_config_declares_what_run_takes(config):
    """The DAG is checked against config.inputs, so it must match run's signature."""
    params = inspect.signature(MAPPING[config].run).parameters
    assert {k: p.annotation for k, p in params.items() if k != "self"} == config.inputs
    assert config.categories
    # Gap G: a ToolRequest demands a worked example for a node that does not exist, so
    # a node that does exist must supply one too, or the agent reasons better about
    # hypothetical tools than about real ones.
    assert config.example
    assert config.model_json_schema()["x-node"] == config.contract()


async def test_a_step_does_not_wait_for_an_unrelated_slow_step():
    """slow and fast start together; after_fast must not wait for slow."""
    ran: list[str] = []

    @activity.defn(name="run_tool")
    async def timed_tool(inp: RunNodeInput) -> Value:
        assert isinstance(inp.config, DnaToProteinConfig)
        # Mark slow/fast by checking the input sequence
        is_slow = len(inp.inputs["sequence"].sequence) > 10
        await asyncio.sleep(1 if is_slow else 0)
        ran.append("slow" if is_slow else "fast")
        return run_tool(inp)

    dag = {
        "inputs": {"fast_seq": "dna", "slow_seq": "dna"},
        "steps": {
            "slow": {"config": {"name": "dna_to_protein"}, "inputs": {"sequence": "slow_seq"}},
            "fast": {"config": {"name": "dna_to_protein"}, "inputs": {"sequence": "fast_seq"}},
        },
    }
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[timed_tool, save_workflow],
        ),
    ):
        await env.client.execute_workflow(
            DagWorkflow.run,
            DagInput(
                dag=Dag.model_validate(dag),
                inputs={
                    "fast_seq": Dna(sequence="ATG"),
                    "slow_seq": Dna(sequence="ATGATGATGATGATGATGATGATGATGATG")
                }
            ),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
    assert ran == ["fast", "slow"]


async def test_progress_reports_each_step_while_running():
    """While slow runs, the query shows it running and the other steps done."""
    release = asyncio.Event()

    @activity.defn(name="run_tool")
    async def gated_tool(inp: RunNodeInput) -> Value:
        assert isinstance(inp.config, DnaToProteinConfig)
        # Mark slow based on sequence length
        is_slow = len(inp.inputs["sequence"].sequence) > 10
        if is_slow:
            await release.wait()
        return run_tool(inp)

    dag = {
        "inputs": {"fast_seq": "dna", "slow_seq": "dna"},
        "steps": {
            "slow": {"config": {"name": "dna_to_protein"}, "inputs": {"sequence": "slow_seq"}},
            "fast": {"config": {"name": "dna_to_protein"}, "inputs": {"sequence": "fast_seq"}},
        },
    }
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[gated_tool, save_workflow],
        ),
    ):
        handle = await env.client.start_workflow(
            DagWorkflow.run,
            DagInput(
                dag=Dag.model_validate(dag),
                inputs={
                    "fast_seq": Dna(sequence="ATG"),
                    "slow_seq": Dna(sequence="ATGATGATGATGATGATGATGATGATGATG")
                }
            ),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
        for _ in range(100):
            progress = await handle.query(DagWorkflow.progress)
            if progress.steps["fast"] == "done":
                break
            await asyncio.sleep(0.05)
        assert progress.steps["fast"] == "done"
        assert progress.steps["slow"] == "running"
        assert progress.values["fast"] == AminoAcidSequence(sequence="M")
        release.set()
        await handle.result()
        progress = await handle.query(DagWorkflow.progress)
    assert set(progress.steps.values()) == {"done"}
