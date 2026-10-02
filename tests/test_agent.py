import json
from concurrent.futures import ThreadPoolExecutor

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.agent import Hypothesis, build_agent
from node_dag.dag import Dag
from node_dag.types import FooBar
from temporal.dag.activities import run_decision, run_tool, save_workflow
from temporal.dag.workflow import TASK_QUEUE, DagWorkflow
from temporal.run_hypothesis import hypotheses_dir, run_hypothesis, save_hypothesis
from temporal.ui.app import _hypothesis_row

BAD = {  # total.b reads a Baz, but port b takes FooBar.
    "inputs": {"x": "foo_bar"},
    "steps": {
        "label": {"config": {"name": "to_baz"}, "inputs": {"value": "x"}},
        "total": {"config": {"name": "sum"}, "inputs": {"a": "x", "b": "label"}},
    },
}
GOOD = {
    "inputs": {"x": "foo_bar"},
    "steps": {
        "total": {"config": {"name": "sum"}, "inputs": {"a": "x", "b": "x"}},
        "label": {"config": {"name": "to_baz"}, "inputs": {"value": "total"}},
    },
}
DOUBLE = {
    "inputs": {"x": "foo_bar"},
    "steps": {"twice": {"config": {"name": "sum"}, "inputs": {"a": "x", "b": "x"}}},
}


def _reply(info: AgentInfo, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


def _submit(info: AgentInfo, dag: dict) -> ModelResponse:
    return _reply(info, {"hypothesis": "sum x with itself", **dag})


async def test_agent_reads_the_catalogue_and_fixes_a_rejected_dag():
    """A scripted model: list, describe, submit a bad DAG, read the error, fix it."""
    seen: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(
            str(p.content)
            for p in messages[-1].parts
            if isinstance(p, ToolReturnPart | RetryPromptPart)
        )
        turn = sum(isinstance(m, ModelResponse) for m in messages)
        if turn < 2:
            name, args = [("list_nodes", {}), ("describe_node", {"name": "sum"})][turn]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return _submit(info, [BAD, GOOD][turn - 2])

    agent = build_agent(FunctionModel(script))
    hyp = Hypothesis(goal="x + x, as a label", inputs={"x": FooBar(count=1)})
    out = (await agent.run(hyp.goal, deps=hyp)).output

    assert out.dag == Dag.model_validate(GOOD)
    assert out.hypothesis == "sum x with itself"
    listing, schema, error = seen
    assert '"inputs": {"a": "foo_bar", "b": "foo_bar"}' in listing
    assert "'x-node'" in schema  # The ports reach the model with the schema.
    assert "port 'b' takes FooBar, but 'label' gives Baz" in error


async def test_run_hypothesis_builds_runs_and_verifies():
    """The verifier gets the outcome of the run, and its verdict lands on the Hypothesis."""

    def builder(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _submit(info, DOUBLE)

    def verifier(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(
            p.content for p in messages[0].parts if isinstance(p, UserPromptPart)
        )
        sent = json.loads(str(prompt))
        assert sent["hypothesis"] == "sum x with itself"
        got = sent["outcome"]["values"]["twice"]["count"]
        want = 2 * sent["inputs"]["x"]["count"]
        return _reply(info, {"achieved": got == want, "reason": f"{got} vs {want}"})

    hyp = Hypothesis(goal="Double x.", inputs={"x": FooBar(count=21)})
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[DagWorkflow],
                activities=[run_tool, run_decision, save_workflow],
                activity_executor=pool,
            ):
                done = await run_hypothesis(
                    hyp, env.client, FunctionModel(builder), FunctionModel(verifier)
                )

    assert done.hypothesis == "sum x with itself"
    assert done.dag == Dag.model_validate(DOUBLE)
    assert done.outcome is not None
    assert done.outcome.values["twice"] == FooBar(count=42)
    assert done.verdict is not None
    assert done.verdict.achieved
    assert done.verdict.reason == "42 vs 42"

    # Saved, with its run, for the hypotheses page.
    (path,) = hypotheses_dir().iterdir()
    row = _hypothesis_row(path)
    assert row.hypothesis == done
    assert row.status == "achieved"
    assert row.progress is not None
    assert row.progress.steps == {"twice": "done"}


def test_a_hypothesis_without_a_dag_is_building():
    hyp = Hypothesis(goal="Double x.", inputs={"x": FooBar(count=1)})
    save_hypothesis(hyp)
    (path,) = hypotheses_dir().iterdir()
    assert _hypothesis_row(path).status == "building"
