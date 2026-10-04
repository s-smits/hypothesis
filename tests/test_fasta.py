from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from temporalio.client import Client

from node_dag.agent import Hypothesis, inputs_agent
from node_dag.nodes.tools.fasta_to_proteins.config import FastaToProteinsConfig
from node_dag.nodes.tools.fasta_to_proteins.function import FastaToProteins
from node_dag.types import TYPES, AminoAcidSequence, Dna, FastaFile, Value
from temporal.dag.activities import results_subdir
from temporal.hypothesis import activities
from temporal.hypothesis.activities import Stage, draft_inputs
from temporal.ui.app import make_app

TWO = ">kinaseA\nMKVLAAG\n>kinaseB\nMSTAV\n"


def _save(file: FastaFile) -> None:
    d = results_subdir("files")
    d.mkdir(parents=True, exist_ok=True)
    (d / file.id).write_text(file.sequence)


def _no_agent(monkeypatch, found=None):
    """An inputs agent that adds nothing, or what the caller scripted."""

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("done")])

    agent, f = inputs_agent(FunctionModel(script))
    monkeypatch.setattr(activities, "inputs_agent", lambda model: (agent, f))
    return f


# --- FastaFile ---------------------------------------------------------------


def test_a_fasta_file_hashes_its_text_not_its_name():
    a = FastaFile(sequence=TWO, name="one.fa")
    b = FastaFile(sequence=TWO, name="another name.fa")
    assert a.id == b.id and a.name != b.name
    assert FastaFile(sequence=TWO + "\n").id != a.id


def test_a_fasta_file_needs_a_record_header():
    with pytest.raises(ValidationError, match="no >header"):
        FastaFile(sequence="MKVLAAG")


def test_a_fasta_file_is_a_value_and_in_types():
    assert TYPES["fasta_file"] is FastaFile
    file = FastaFile(sequence=TWO, name="k.fa")
    back = TypeAdapter(Value).validate_python(file.model_dump())
    assert back == file


# --- fasta_to_proteins --------------------------------------------------------


def test_fasta_to_proteins_reads_every_record_headers_dropped():
    out = FastaToProteins(FastaToProteinsConfig()).run(
        file=[FastaFile(sequence=">a some note\nmkvl\n>b\nMSTA\n", name="two.fa")]
    )
    assert out == [
        AminoAcidSequence(sequence="MKVL"),
        AminoAcidSequence(sequence="MSTA"),
    ]


def test_fasta_to_proteins_names_a_bad_record_in_its_error():
    with pytest.raises(ValueError, match="'x' in bad.fa is not amino acids"):
        FastaToProteins(FastaToProteinsConfig()).run(
            file=[FastaFile(sequence=">x\nMKV4\n", name="bad.fa")]
        )


def test_fasta_to_proteins_errors_on_an_empty_record():
    with pytest.raises(ValueError, match="'x' in bad.fa holds no sequence"):
        FastaToProteins(FastaToProteinsConfig()).run(
            file=[FastaFile(sequence=">x\n>y\nMKV\n", name="bad.fa")]
        )


def test_describe_inputs_shows_the_ends_and_length_of_a_long_file():
    """A file is too big for a prompt: the builder sees its ends and its length."""
    long_text = ">a\n" + "M" * 5000
    hyp = Hypothesis(
        goal="g", inputs={"file": [FastaFile(sequence=long_text, name="big.fa")]}
    )

    (shown,) = hyp.describe_inputs()["file"]["sequences"]
    assert len(shown) < len(long_text) and f"{len(long_text)} long" in shown


# --- FASTAFile mentions -------------------------------------------------------


async def test_a_fasta_mention_becomes_an_input_from_the_saved_file(
    results_dir, monkeypatch
):
    _no_agent(monkeypatch)
    file = FastaFile(sequence=TWO, name="kinases.fasta")
    _save(file)

    hyp = Hypothesis(goal=f"check each sequence in FASTAFile {file.id} (kinases.fasta)")
    out = await draft_inputs(Stage(hyp=hyp, model="test"))

    assert out.inputs == {"kinases": [file]}
    assert out.sources == {"kinases": "the file kinases.fasta attached to the goal"}


async def test_the_same_file_mentioned_twice_is_still_one_input(
    results_dir, monkeypatch
):
    _no_agent(monkeypatch)
    file = FastaFile(sequence=TWO)
    _save(file)

    hyp = Hypothesis(goal=f"take FASTAFile {file.id} and FASTAFile {file.id} together")
    out = await draft_inputs(Stage(hyp=hyp, model="test"))

    assert out.inputs == {"file": [file]}
    assert out.sources == {"file": "a file attached to the goal"}


async def test_a_mention_of_a_file_that_is_not_saved_fails_the_run(results_dir):
    hyp = Hypothesis(goal="fold FASTAFile 0123456789ab")
    out = await draft_inputs(Stage(hyp=hyp, model="test"))

    assert not out.inputs
    assert out.error == (
        "the goal names FASTAFile 0123456789ab "
        "but nothing under results/files has that id"
    )


async def test_a_file_saved_under_a_different_id_counts_as_missing(results_dir):
    """An id is a hash of the contents: edited contents are not that file."""
    _save(FastaFile(sequence=">x\nMKV\n"))
    # Rewrite the saved file with contents its name does not hash to.
    other = FastaFile(sequence=TWO)
    d = results_subdir("files")
    (d / other.id).write_text(">x\nCHANGED\n")

    hyp = Hypothesis(goal=f"fold FASTAFile {other.id}")
    out = await draft_inputs(Stage(hyp=hyp, model="test"))

    assert not out.inputs
    assert out.error is not None and f"FASTAFile {other.id}" in out.error


async def test_an_attached_file_shares_the_inputs_with_what_the_agent_found(
    results_dir, monkeypatch
):
    file = FastaFile(sequence=TWO, name="kinases.fasta")
    _save(file)

    replies = iter(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "add_input",
                        {
                            "name": "kinases",
                            "source": "given in the goal",
                            "entities": [{"kind": "dna", "sequence": "ATG"}],
                        },
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("done")]),
        ]
    )

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return next(replies)

    agent, found = inputs_agent(FunctionModel(script))
    monkeypatch.setattr(activities, "inputs_agent", lambda model: (agent, found))

    hyp = Hypothesis(
        goal=f"translate ATG and check FASTAFile {file.id} (kinases.fasta)"
    )
    out = await draft_inputs(Stage(hyp=hyp, model="test"))

    # The file cannot take the agent's name, so it is made unique.
    assert out.inputs["kinases"] == [Dna(sequence="ATG")]
    assert out.inputs["kinases_2"] == [file]


# --- the upload endpoint -----------------------------------------------------


def _files_endpoint():
    app = make_app(cast(Client, None))
    return next(
        r.endpoint
        for r in app.routes
        if isinstance(r, APIRoute) and r.path == "/api/files"
    )


async def test_a_dropped_file_is_saved_under_its_content_hash(results_dir):
    saved = await _files_endpoint()(name="kinases.fasta", data=TWO.encode())

    file = FastaFile(sequence=TWO, name="kinases.fasta")
    assert saved.id == file.id and saved.kind == "fasta_file"
    assert (results_subdir("files") / saved.id).read_bytes() == TWO.encode()


async def test_an_upload_that_is_not_fasta_is_refused(results_dir):
    with pytest.raises(HTTPException) as e:
        await _files_endpoint()(name="notes.txt", data=b"no header")

    assert e.value.status_code == 422
    assert not list(results_subdir("files").glob("*"))
