"""The payload offload: big results become files under ``payloads/``, not gRPC bytes.

A ``run_tool`` result of twenty folded structures used to end the workflow with
PayloadsTooLarge (TMPRL1103); now anything over the threshold is a file and a
reference, and every reader sees the payload itself again.
"""

import pytest
from temporalio import activity
from temporalio.api.common.v1 import Payload
from temporalio.converter import (
    StorageDriverRetrieveContext,
    StorageDriverStoreContext,
)
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from node_dag.dag import Dag, DagInput
from node_dag.types import AminoAcidSequence, Dna, ProteinStructure, Value
from temporal.dag.activities import RunNodeInput, SavedRun, save_workflow
from temporal.dag.workflow import DagWorkflow
from temporal.payloads import ResultsDriver, data_converter

# An mmCIF for a few hundred residues is this size; twenty of them was the failure.
BIG = ProteinStructure(sequence="MALK", structure="x" * 300_000)


async def test_over_the_threshold_is_a_file_and_a_reference(results_dir):
    [payload] = await data_converter.encode([[BIG]])

    assert payload.ByteSize() < 10_000
    files = list((results_dir / "payloads").iterdir())
    assert len(files) == 1
    [back] = await data_converter.decode([payload], [list[ProteinStructure]])
    assert back == [BIG]


async def test_under_the_threshold_is_left_alone(results_dir):
    [payload] = await data_converter.encode([Dna(sequence="ATG")])

    assert not (results_dir / "payloads").exists()
    [back] = await data_converter.decode([payload], [Dna])
    assert back == Dna(sequence="ATG")


async def test_a_reference_with_no_file_says_where_it_looked(results_dir):
    # The claim names the file; nothing under it means the run cannot be replayed.
    driver = ResultsDriver()
    [claim] = await driver.store(StorageDriverStoreContext(), [Payload()])
    (results_dir / "payloads" / claim.claim_data["file"]).unlink()

    with pytest.raises(OSError, match=claim.claim_data["file"]):
        await driver.retrieve(StorageDriverRetrieveContext(), [claim])


def test_the_driver_name_is_stable():
    # Recorded in every reference left in a run's history: changing it orphans them.
    assert ResultsDriver().name() == "results"


async def test_a_fold_sized_result_survives_the_workflow(results_dir):
    # The step that failed: a tool result bigger than Temporal's payload limit.
    folded: list[Value] = [
        BIG,
        ProteinStructure(sequence="MALKL", structure="y" * 300_000),
    ]

    @activity.defn(name="run_tool")
    async def folded_stub(_inp: RunNodeInput) -> list[Value]:
        return folded

    dag = Dag.model_validate(
        {
            "inputs": {"seqs": "amino_acid_sequence"},
            "steps": {
                "folded": {
                    "config": {"name": "esmfold2_fold"},
                    "inputs": {"sequence": "seqs"},
                }
            },
        }
    )
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t",
            workflows=[DagWorkflow],
            activities=[folded_stub, save_workflow],
        ),
    ):
        out = await env.client.execute_workflow(
            DagWorkflow.run,
            DagInput(
                dag=dag,
                inputs={"seqs": [AminoAcidSequence(sequence="MALK")]},
            ),
            id="big-1",
            task_queue="t",
        )

    # The result reached the client whole, and the save saw the same entities.
    assert out.values["folded"].items == folded
    assert list((results_dir / "payloads").iterdir())
    saved = SavedRun.model_validate_json(
        (results_dir / "workflows" / "big-1.json").read_text()
    )
    assert saved.values["folded"].items == folded
