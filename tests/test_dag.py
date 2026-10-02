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
from node_dag.nodes.tools.add.config import AddConfig
from node_dag.types import Baz, FooBar, Value
from temporal.dag.activities import RunNodeInput, run_decision, run_tool, save_workflow
from temporal.dag.workflow import DagWorkflow

# x + 5; if >= 10: sum(it, y) -> Baz; else: it - 1
DAG: dict = {
    "inputs": {"x": "foo_bar", "y": "foo_bar"},
    "steps": {
        "add5": {"config": {"name": "add", "amount": 5}, "inputs": {"value": "x"}},
        "big": {
            "config": {"name": "at_least", "threshold": 10},
            "inputs": {"value": "add5"},
        },
        "total": {"config": {"name": "sum"}, "inputs": {"a": "big.yes", "b": "y"}},
        "label": {
            "config": {"name": "to_baz", "prefix": "n"},
            "inputs": {"value": "total"},
        },
        "sub1": {
            "config": {"name": "add", "amount": -1},
            "inputs": {"value": "big.no"},
        },
    },
}


@pytest.mark.parametrize(
    ("x", "key", "value", "skipped"),
    [
        (5, "label", Baz(label="n12"), ["sub1"]),
        (1, "sub1", FooBar(count=5), ["label", "total"]),
    ],
)
async def test_workflow_routes_and_skips(x, key, value, skipped, results_dir):
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
                        inputs={"x": FooBar(count=x), "y": FooBar(count=2)},
                    ),
                    id="run-1",
                    task_queue="t",
                )
    assert out.values[key] == value
    assert out.skipped == skipped
    saved = DagProgress.model_validate_json(
        (results_dir / "workflows" / "run-1.json").read_text()
    )
    assert saved.values == out.values
    assert sorted(k for k, s in saved.steps.items() if s == "skipped") == skipped


def _step(name: str, inputs: dict, **config) -> dict:
    return {"config": {"name": name, **config}, "inputs": inputs}


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"add5": _step("add", {"value": "sub1"}, amount=5)}, "Cycle"),
        ({"sub1": _step("add", {"value": "nope"}, amount=1)}, "Unknown source"),
        ({"sub1": _step("add", {"value": "big"}, amount=1)}, "as '<step>.yes'"),
        ({"sub1": _step("add", {"value": "label"}, amount=1)}, "takes FooBar"),
        ({"total": _step("sum", {"a": "big.yes"})}, "ports"),
        ({"x": _step("add", {"value": "y"}, amount=1)}, "repeat an input"),
    ],
)
def test_dag_rejects_bad_graphs(patch, match):
    with pytest.raises(ValidationError, match=match):
        Dag.model_validate({**DAG, "steps": {**DAG["steps"], **patch}})


def test_dag_input_must_match_declared_types():
    with pytest.raises(ValidationError, match="DAG wants inputs"):
        DagInput(
            dag=Dag.model_validate(DAG),
            inputs={"x": Baz(label="a"), "y": FooBar(count=2)},
        )


@pytest.mark.parametrize("config", MAPPING)
def test_config_declares_what_run_takes(config):
    """The DAG is checked against config.inputs, so it must match run's signature."""
    params = inspect.signature(MAPPING[config].run).parameters
    assert {k: p.annotation for k, p in params.items() if k != "self"} == config.inputs
    assert config.categories
    assert config.model_json_schema()["x-node"] == config.contract()


async def test_a_step_does_not_wait_for_an_unrelated_slow_step():
    """slow and fast start together; after_fast must not wait for slow."""
    ran: list[int] = []

    @activity.defn(name="run_tool")
    async def timed_tool(inp: RunNodeInput) -> Value:
        assert isinstance(inp.config, AddConfig)
        await asyncio.sleep(1 if inp.config.amount == 1000 else 0)
        ran.append(inp.config.amount)
        return FooBar(count=0)

    dag = {
        "inputs": {"x": "foo_bar"},
        "steps": {
            "slow": _step("add", {"value": "x"}, amount=1000),
            "fast": _step("add", {"value": "x"}, amount=1),
            "after_fast": _step("add", {"value": "fast"}, amount=2),
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
            DagInput(dag=Dag.model_validate(dag), inputs={"x": FooBar(count=0)}),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
    assert ran == [1, 2, 1000]


async def test_progress_reports_each_step_while_running():
    """While slow runs, the query shows it running and the other steps done."""
    release = asyncio.Event()

    @activity.defn(name="run_tool")
    async def gated_tool(inp: RunNodeInput) -> Value:
        assert isinstance(inp.config, AddConfig)
        if inp.config.amount == 1000:
            await release.wait()
        return FooBar(count=inp.config.amount)

    dag = {
        "inputs": {"x": "foo_bar"},
        "steps": {
            "slow": _step("add", {"value": "x"}, amount=1000),
            "fast": _step("add", {"value": "x"}, amount=1),
            "after_fast": _step("add", {"value": "fast"}, amount=2),
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
            DagInput(dag=Dag.model_validate(dag), inputs={"x": FooBar(count=0)}),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
        for _ in range(100):
            progress = await handle.query(DagWorkflow.progress)
            if progress.steps["after_fast"] == "done":
                break
            await asyncio.sleep(0.05)
        assert progress.steps == {
            "slow": "running",
            "fast": "done",
            "after_fast": "done",
        }
        assert progress.values["after_fast"] == FooBar(count=2)
        release.set()
        await handle.result()
        progress = await handle.query(DagWorkflow.progress)
    assert set(progress.steps.values()) == {"done"}
