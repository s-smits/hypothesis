from pathlib import Path
from typing import cast

from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from temporalio.client import Client

from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.registry import Registry
from node_dag.types import Dna
from temporal.run_hypothesis import registry_dir
from temporal.ui.app import make_app


def _endpoint(path: str):
    # These routes do not use the Temporal client.
    app = make_app(cast(Client, None))
    return next(
        r.endpoint for r in app.routes if isinstance(r, APIRoute) and r.path == path
    )


async def test_nodes_api_lists_what_the_builder_registered(results_dir):
    assert await _endpoint("/api/nodes")() == []
    score = DnaAtomScoreConfig(reference=Dna(sequence="ATGGCTCTGAAATAA"))
    column = score.columns()["atom_count"]
    registry = Registry(registry_dir())
    registry.register(score, "atoms and protein changes")
    registry.register(AtMostConfig(column=column, threshold=400), "small ones")

    nodes = await _endpoint("/api/nodes")()

    scorer, filt = sorted(nodes, key=lambda n: n["config"]["name"] != "dna_atom_score")
    assert scorer["node"] == f"dna_atom_score__{score.config_hash}"
    assert scorer["description"] == "atoms and protein changes"
    assert scorer["categories"] == ["scoring"]
    assert scorer["score_columns"]["atom_count"] == column
    assert filt["filters_on"] == column
    assert filt["categories"] == ["filter"]


async def test_nodes_page_is_served_and_linked_from_every_page():
    page = await _endpoint("/nodes")()
    assert isinstance(page, FileResponse)
    ui = Path(page.path).parent
    assert "/api/nodes" in (ui / "nodes.html").read_text()
    for name in ("index", "hypotheses", "new", "nodes"):
        assert (ui / f"{name}.html").read_text().count('<a href="/nodes"') == 1
