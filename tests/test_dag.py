import asyncio
import inspect
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar, Literal

import pytest
from Bio.Seq import Seq
from pydantic import ValidationError
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.dag import Dag, DagInput, DagOutput
from node_dag.factory import MAPPING
from node_dag.nodes.base import BaseFilterConfig, BaseToolConfig
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.ostir_expression.function import OstirExpression
from node_dag.types import (
    AminoAcidSequence,
    Dna,
    NucleicAcid,
    ProteinStructure,
    Table,
    Value,
)
from temporal.dag.activities import (
    RunNodeInput,
    SavedRun,
    SaveWorkflowInput,
    run_filter,
    run_score,
    run_tool,
    save_workflow,
)
from temporal.dag.workflow import DagWorkflow

REF = Dna(sequence="ATGGCTCTGAAATAA")  # M A L K *
SEQS = [REF, Dna(sequence="ATGGCCCTGAAATAA"), Dna(sequence="ATGGCGTTAAAGTAG")]
MUTATE = MutateSynonymousConfig(seed=3, count=2)
MUTANTS = MutateSynonymous(MUTATE).run(sequence=SEQS)
SCORE = OstirExpressionConfig(utr="TTCTAGAAAGGAGGTAAAAAA")
EXPRESSION = SCORE.columns()["expression"]
SCORES = {
    m.id: s["expression"].value
    for m, s in zip(
        MUTANTS, OstirExpression(SCORE).run(sequence=list[NucleicAcid](MUTANTS))
    )
}
# Splits the mutants: at least one is at or under it, and at least one is over.
LIMIT = 600_000.0
ACTIVITIES = [run_tool, run_score, run_filter, save_workflow]


def _step(config: dict, source: str, port: str = "sequence") -> dict:
    return {"config": config, "inputs": {port: source}}


# Make 2 changes to each, score them, keep the expressing ones, then the moderate ones.
DAG: dict = {
    "inputs": {"seqs": "dna"},
    "steps": {
        "mutated": _step(MUTATE.model_dump(mode="json"), "seqs"),
        "scored": _step(SCORE.model_dump(mode="json"), "mutated"),
        "expressed": _step(
            {"name": "at_least", "column": EXPRESSION, "threshold": 0},
            "scored",
            "items",
        ),
        "small": _step(
            {"name": "at_most", "column": EXPRESSION, "threshold": LIMIT},
            "expressed.yes",
            "items",
        ),
        "protein": _step({"name": "dna_to_protein"}, "small.yes"),
    },
}


async def _run(dag: dict, seqs: list[Dna], workflow_id: str = "run-1"):
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        with ThreadPoolExecutor() as pool:
            async with Worker(
                env.client,
                task_queue="t",
                workflows=[DagWorkflow],
                activities=ACTIVITIES,
                activity_executor=pool,
            ):
                return await env.client.execute_workflow(
                    DagWorkflow.run,
                    DagInput(dag=Dag.model_validate(dag), inputs={"seqs": seqs}),
                    id=workflow_id,
                    task_queue="t",
                )


async def test_workflow_runs_every_entity_through_each_step(results_dir):
    out = await _run(DAG, SEQS)

    assert out.values["seqs"].items == SEQS
    mutated, scored = out.values["mutated"], out.values["scored"]
    assert mutated.items == MUTANTS
    assert all(
        str(Seq(m.sequence).translate()) == str(Seq(REF.sequence).translate())
        for m in mutated.items
    )
    assert mutated.scores == {}  # New entities have no scores.
    # Scoring keeps the entities and adds a column per score, named by node and hash.
    assert scored.items == mutated.items
    assert set(scored.scores) == {EXPRESSION}
    assert scored.scores[EXPRESSION] == SCORES
    # A filter splits the table and takes the columns of what it keeps.
    yes, no = out.values["expressed.yes"], out.values["expressed.no"]
    assert yes.items == scored.items
    assert no.items == []
    assert yes.scores == scored.scores
    small = out.values["small.yes"]
    assert small.items and all(SCORES[i.id] <= LIMIT for i in small.items)
    assert any(SCORES[i.id] > LIMIT for i in yes.items) is bool(
        out.values["small.no"].items
    )
    split = small.items + out.values["small.no"].items
    assert sorted(i.id for i in split) == sorted(i.id for i in yes.items)
    assert out.values["protein"].items == [
        AminoAcidSequence(sequence=str(Seq(REF.sequence).translate()))
    ]

    saved = SavedRun.model_validate_json(
        (results_dir / "workflows" / "run-1.json").read_text()
    )
    assert saved.values == out.values
    # Enough to show the run once Temporal has forgotten it.
    assert saved.status == "COMPLETED"
    assert saved.error is None
    assert saved.start_time and saved.close_time
    assert saved.start_time <= saved.close_time


async def test_a_step_with_nothing_to_run_on_is_skipped():
    dag = {
        **DAG,
        "steps": {
            **DAG["steps"],
            "small": _step(
                {"name": "at_most", "column": EXPRESSION, "threshold": 0},
                "expressed.yes",
                "items",
            ),
        },
    }
    out = await _run(dag, SEQS)
    assert out.values["small.yes"] == Table()
    assert len(out.values["small.no"].items) == len(SEQS)
    assert out.values["protein"] == Table()
    assert out.skipped == ["protein"]


def _filter(column: str, source: str) -> dict:
    return _step({"name": "at_most", "column": column, "threshold": 1}, source, "items")


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"mutated": _step({"name": "dna_to_protein"}, "nope")}, "Unknown source"),
        ({"mutated": _step({"name": "dna_to_protein"}, "mutated")}, "Cycle"),
        (
            {
                "protein": _step({"name": "dna_to_protein"}, "protein2"),
                "protein2": _step({"name": "dna_to_protein"}, "protein"),
            },
            "Cycle",
        ),
        # A protein is not DNA.
        (
            {
                "p": _step({"name": "dna_to_protein"}, "seqs"),
                "bad": _step({"name": "dna_to_protein"}, "p"),
            },
            "port 'sequence' takes Dna, but 'p' gives AminoAcidSequence",
        ),
        # Nothing has scored the sequences yet.
        ({"early": _filter(EXPRESSION, "seqs")}, "score columns \\[\\]"),
        # A mutation makes new entities, which drops the scores.
        (
            {
                "remut": _step({"name": "mutate_synonymous", "seed": 1}, "scored"),
                "late": _filter(EXPRESSION, "remut"),
            },
            "score columns \\[\\]",
        ),
        # The hash is part of the column, so the column of another config is unknown.
        (
            {
                "wrong": _filter(
                    EXPRESSION.replace(SCORE.config_hash, "00000000"), "scored"
                )
            },
            f"score columns \\['{EXPRESSION}'\\]",
        ),
    ],
)
def test_dag_rejects_bad_graphs(patch, match):
    with pytest.raises(ValidationError, match=match):
        Dag.model_validate({**DAG, "steps": {**DAG["steps"], **patch}})


def test_dag_accepts_filters_on_columns_that_reach_them():
    Dag.model_validate(
        {**DAG, "steps": {**DAG["steps"], "ok": _filter(EXPRESSION, "expressed.no")}}
    )


def test_dag_input_must_match_declared_types():
    dag = Dag.model_validate(DAG)
    with pytest.raises(ValidationError, match="DAG wants inputs"):
        DagInput(dag=dag, inputs={"seqs": [AminoAcidSequence(sequence="MMM")]})
    with pytest.raises(ValidationError, match="DAG wants inputs"):
        DagInput(dag=dag, inputs={"other": SEQS})


def test_dag_input_supports_protein_structure():
    dag = Dag.model_validate(
        {"inputs": {"structures": "protein_structure"}, "steps": {}}
    )
    ps = ProteinStructure(sequence="MALK*", structure="ATOM 1 ...")
    inp = DagInput(dag=dag, inputs={"structures": [ps]})
    assert inp.inputs["structures"] == [ps]


def test_a_config_with_a_made_up_hash_is_rejected():
    step = _step(
        {**SCORE.model_dump(mode="json"), "config_hash": "feedface"}, "mutated"
    )
    with pytest.raises(ValidationError, match="config_hash"):
        Dag.model_validate({**DAG, "steps": {**DAG["steps"], "scored": step}})


@pytest.mark.parametrize("config", MAPPING)
def test_config_declares_what_run_takes(config):
    """The DAG is checked against config.inputs, so it must match run's signature."""
    params = inspect.signature(MAPPING[config].run).parameters
    got = {k: p.annotation for k, p in params.items() if k != "self"}
    want: dict[str, object] = {port: list[t] for port, t in config.inputs.items()}
    if issubclass(config, BaseFilterConfig):
        # One column arrives as a list, several as a dict keyed by column.
        want["values"] = config.values_type
        # A filter that compares with a reference entity's score is also given it.
        if "reference" in got:
            want["reference"] = float
    assert got == want
    assert config.inputs
    assert config.categories
    assert config.model_json_schema()["x-node"] == config.contract()


def test_contract_shows_score_names_and_types():
    assert OstirExpressionConfig.contract()["scores"] == {
        "expression": "score",
    }


async def test_a_step_does_not_wait_for_an_unrelated_slow_step():
    """slow and fast start together; after_fast must not wait for slow."""
    ran: list[str] = []

    @activity.defn(name="run_tool")
    async def timed_tool(inp: RunNodeInput) -> list[Value]:
        is_slow = len(inp.inputs["sequence"][0].sequence) > 10
        await asyncio.sleep(1 if is_slow else 0)
        ran.append("slow" if is_slow else "fast")
        return run_tool(inp)

    dag = {
        "inputs": {"fast_seq": "dna", "slow_seq": "dna"},
        "steps": {
            "slow": _step({"name": "dna_to_protein"}, "slow_seq"),
            "fast": _step({"name": "dna_to_protein"}, "fast_seq"),
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
                    "fast_seq": [Dna(sequence="ATG")],
                    "slow_seq": [Dna(sequence="ATGATGATGATGATGATGATGATGATGATG")],
                },
            ),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
    assert ran == ["fast", "slow"]


async def test_a_failed_run_is_saved_with_its_error(results_dir):
    @activity.defn(name="run_tool")
    async def broken_tool(inp: RunNodeInput) -> list[Value]:
        raise ApplicationError("out of GPUs", non_retryable=True)

    dag = {
        "inputs": {"seq": "dna"},
        "steps": {"protein": _step({"name": "dna_to_protein"}, "seq")},
    }
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[broken_tool, save_workflow],
        ),
    ):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                DagWorkflow.run,
                DagInput(
                    dag=Dag.model_validate(dag), inputs={"seq": [Dna(sequence="ATG")]}
                ),
                id="broken",
                task_queue="t",
            )
    saved = SavedRun.model_validate_json(
        (results_dir / "workflows" / "broken.json").read_text()
    )
    assert saved.status == "FAILED"
    assert saved.error == "out of GPUs"
    assert saved.steps == {"protein": "failed"}


async def _run_when_saving_always_fails(tool, tries: list[int]) -> DagOutput:
    """Run one protein step whose save fails every time, counting the attempts."""

    @activity.defn(name="save_workflow")
    async def full_disk(inp: SaveWorkflowInput) -> None:
        tries.append(1)
        raise OSError("disk full")

    dag = {
        "inputs": {"seq": "dna"},
        "steps": {"protein": _step({"name": "dna_to_protein"}, "seq")},
    }
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[tool, full_disk],
        ),
    ):
        # Bounded, so a save that is retried for ever fails the test, not hangs it.
        return await asyncio.wait_for(
            env.client.execute_workflow(
                DagWorkflow.run,
                DagInput(
                    dag=Dag.model_validate(dag), inputs={"seq": [Dna(sequence="ATG")]}
                ),
                id="no-save",
                task_queue="t",
            ),
            timeout=10,
        )


async def test_a_save_that_keeps_failing_does_not_fail_a_finished_run():
    @activity.defn(name="run_tool")
    async def tool(inp: RunNodeInput) -> list[Value]:
        return run_tool(inp)

    tries: list[int] = []
    out = await _run_when_saving_always_fails(tool, tries)
    assert out.values["protein"].items == [AminoAcidSequence(sequence="M")]
    assert len(tries) == 3


async def test_a_save_that_keeps_failing_does_not_hide_why_a_run_failed():
    @activity.defn(name="run_tool")
    async def broken_tool(inp: RunNodeInput) -> list[Value]:
        raise ApplicationError("out of GPUs", non_retryable=True)

    tries: list[int] = []
    with pytest.raises(WorkflowFailureError) as raised:
        await _run_when_saving_always_fails(broken_tool, tries)
    cause: BaseException = raised.value
    while cause.__cause__:
        cause = cause.__cause__
    assert str(cause) == "out of GPUs"
    assert len(tries) == 3


async def test_a_cancel_during_the_final_save_still_cancels_the_run():
    @activity.defn(name="run_tool")
    async def tool(inp: RunNodeInput) -> list[Value]:
        return run_tool(inp)

    saving, release = asyncio.Event(), asyncio.Event()

    @activity.defn(name="save_workflow")
    async def slow_save(inp: SaveWorkflowInput) -> None:
        saving.set()
        await release.wait()

    dag = {
        "inputs": {"seq": "dna"},
        "steps": {"protein": _step({"name": "dna_to_protein"}, "seq")},
    }
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[tool, slow_save],
        ),
    ):
        handle = await env.client.start_workflow(
            DagWorkflow.run,
            DagInput(
                dag=Dag.model_validate(dag), inputs={"seq": [Dna(sequence="ATG")]}
            ),
            id="cancel-in-save",
            task_queue="t",
        )
        try:
            await asyncio.wait_for(saving.wait(), timeout=10)
            await handle.cancel()
            # Bounded, so a lost cancel fails the test, not hangs it.
            with pytest.raises(WorkflowFailureError) as raised:
                await asyncio.wait_for(handle.result(), timeout=10)
            assert isinstance(raised.value.cause, CancelledError)
            status = (await handle.describe()).status
            assert status == WorkflowExecutionStatus.CANCELED
        finally:
            release.set()


async def test_progress_reports_each_step_while_running():
    """While slow runs, the query shows it running and the other steps done."""
    release = asyncio.Event()

    @activity.defn(name="run_tool")
    async def gated_tool(inp: RunNodeInput) -> list[Value]:
        if len(inp.inputs["sequence"][0].sequence) > 10:
            await release.wait()
        return run_tool(inp)

    dag = {
        "inputs": {"fast_seq": "dna", "slow_seq": "dna"},
        "steps": {
            "slow": _step({"name": "dna_to_protein"}, "slow_seq"),
            "fast": _step({"name": "dna_to_protein"}, "fast_seq"),
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
                    "fast_seq": [Dna(sequence="ATG")],
                    "slow_seq": [Dna(sequence="ATGATGATGATGATGATGATGATGATGATG")],
                },
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
        assert progress.values["fast"].items == [AminoAcidSequence(sequence="M")]
        release.set()
        await handle.result()
        progress = await handle.query(DagWorkflow.progress)
    assert set(progress.steps.values()) == {"done"}


# dna_to_protein, given a second port, stands in for a tool that takes two lists.
TWO_PORTS = {"sequence": Dna, "partner": AminoAcidSequence}


def _two_port_step(sequence: str, partner: str) -> dict:
    return {
        "config": {"name": "dna_to_protein"},
        "inputs": {"sequence": sequence, "partner": partner},
    }


@pytest.fixture
def two_ports(monkeypatch):
    monkeypatch.setattr(DnaToProteinConfig, "inputs", TWO_PORTS)


@pytest.mark.parametrize(
    ("inputs", "match"),
    [
        ({"sequence": "seqs"}, "ports \\['sequence'\\] != dna_to_protein ports"),
        (
            {"sequence": "seqs", "partner": "seqs.yes"},
            "port 'partner': unknown source 'seqs.yes'",
        ),
        (
            {"sequence": "seqs", "partner": "seqs"},
            "port 'partner' takes AminoAcidSequence, but 'seqs' gives Dna",
        ),
    ],
)
def test_dag_checks_every_port_of_a_tool(two_ports, inputs, match):
    step = {"config": {"name": "dna_to_protein"}, "inputs": inputs}
    with pytest.raises(ValidationError, match=match):
        Dag.model_validate({"inputs": {"seqs": "dna"}, "steps": {"both": step}})


def test_a_score_or_filter_must_have_one_port():
    with pytest.raises(TypeError, match="must have one input port"):

        class TwoPortFilter(BaseFilterConfig):
            name: Literal["two_port_filter"] = "two_port_filter"
            inputs: ClassVar = TWO_PORTS


async def _run_two_ports(
    seqs: list[Dna], partners: list[AminoAcidSequence]
) -> tuple[DagOutput, list[RunNodeInput]]:
    """Run a DAG whose one step takes both lists, recording what the tool was given."""
    calls: list[RunNodeInput] = []

    @activity.defn(name="run_tool")
    async def recording_tool(inp: RunNodeInput) -> list[Value]:
        calls.append(inp)
        return inp.inputs["partner"]

    dag = {
        "inputs": {"seqs": "dna", "partners": "amino_acid_sequence"},
        "steps": {"both": _two_port_step("seqs", "partners")},
    }
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[recording_tool, save_workflow],
        ),
    ):
        out = await env.client.execute_workflow(
            DagWorkflow.run,
            DagInput(
                dag=Dag.model_validate(dag),
                inputs={"seqs": seqs, "partners": partners},
            ),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
    return out, calls


async def test_a_tool_gets_the_whole_list_on_each_port(two_ports):
    partners = [AminoAcidSequence(sequence="MALK"), AminoAcidSequence(sequence="MK")]
    out, (call,) = await _run_two_ports(SEQS, partners)
    assert call.inputs == {"sequence": SEQS, "partner": partners}
    assert out.values["both"].items == partners


async def test_a_tool_with_an_empty_port_is_skipped(two_ports):
    out, calls = await _run_two_ports(SEQS, [])
    assert calls == []
    assert out.skipped == ["both"]
    assert out.values["both"] == Table()


@pytest.fixture
def optional_port(monkeypatch):
    """dna_to_protein with a second port a plan may leave out."""
    monkeypatch.setattr(DnaToProteinConfig, "inputs", TWO_PORTS)
    monkeypatch.setattr(DnaToProteinConfig, "optional_inputs", ("partner",))


def test_a_plan_may_leave_an_optional_port_out(optional_port):
    dag = Dag.model_validate(
        {
            "inputs": {"seqs": "dna"},
            "steps": {
                "both": {
                    "config": {"name": "dna_to_protein"},
                    "inputs": {"sequence": "seqs"},
                }
            },
        }
    )
    # An unwired port is nothing to wait for, so it adds no dependency.
    assert dag.steps["both"].deps() == {"seqs"}


def test_a_required_port_is_still_required(optional_port):
    step = {"config": {"name": "dna_to_protein"}, "inputs": {"partner": "prots"}}
    with pytest.raises(ValidationError, match=r"\['partner'\] may be left out"):
        Dag.model_validate(
            {"inputs": {"prots": "amino_acid_sequence"}, "steps": {"both": step}}
        )


def test_an_optional_port_must_be_a_port_the_node_has():
    with pytest.raises(TypeError, match="optional ports it has no input for"):

        class StrayOptional(BaseToolConfig):
            name: Literal["stray_optional"] = "stray_optional"
            inputs: ClassVar = {"sequence": Dna}
            optional_inputs: ClassVar = ("partner",)
            output: ClassVar = Dna


def test_a_tool_cannot_make_every_port_optional():
    with pytest.raises(TypeError, match="must keep one port required"):

        class AllOptional(BaseToolConfig):
            name: Literal["all_optional"] = "all_optional"
            inputs: ClassVar = {"sequence": Dna}
            optional_inputs: ClassVar = ("sequence",)
            output: ClassVar = Dna


def test_a_score_or_filter_port_cannot_be_optional():
    with pytest.raises(TypeError, match="one port cannot be optional"):

        class OptionalFilter(BaseFilterConfig):
            name: Literal["optional_filter"] = "optional_filter"
            inputs: ClassVar = {"items": Dna}
            optional_inputs: ClassVar = ("items",)


async def test_an_unwired_optional_port_reaches_the_node_empty(optional_port):
    """The node is run, not skipped, and still gets every port its config declares."""
    calls: list[RunNodeInput] = []

    @activity.defn(name="run_tool")
    async def recording_tool(inp: RunNodeInput) -> list[Value]:
        calls.append(inp)
        return inp.inputs["sequence"]

    dag = {
        "inputs": {"seqs": "dna"},
        "steps": {
            "both": {
                "config": {"name": "dna_to_protein"},
                "inputs": {"sequence": "seqs"},
            }
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
            activities=[recording_tool, save_workflow],
        ),
    ):
        out = await env.client.execute_workflow(
            DagWorkflow.run,
            DagInput(dag=Dag.model_validate(dag), inputs={"seqs": SEQS}),
            id=str(uuid.uuid4()),
            task_queue="t",
        )
    assert out.skipped == []
    (call,) = calls
    assert call.inputs == {"sequence": SEQS, "partner": []}


async def test_an_empty_required_port_still_skips_the_step(optional_port):
    out, calls = await _run_two_ports([], [AminoAcidSequence(sequence="MALK")])
    assert calls == []
    assert out.skipped == ["both"]
