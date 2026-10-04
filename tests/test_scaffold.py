import importlib.util
import sys
from types import ModuleType
from typing import Any

import pytest
from click.testing import CliRunner

from node_dag.plan import ToolRequest
from node_dag.types import Dna
from temporal.scaffold_node import main

WHY = {
    "why_needed": "nothing counts GC",
    "why_not_composable": "codon_count counts codons, not bases",
}
TOOL = ToolRequest(
    name="trim_stops",
    node="tool",
    purpose="Cut each sequence after its first in-frame stop codon.",
    kind="dna",
    output="dna",
    example="ATGTAAGCT -> ATGTAA",
    config_fields=[
        {
            "name": "stops",
            "type": "list[str]",
            "description": "The stop codons.",
            "required": False,
            "default": ["TAA"],
        }
    ],
    **WHY,
)
SCORE = ToolRequest(
    name="gc_count",
    node="score",
    purpose="Score each sequence by its GC bases.",
    kind="dna",
    output=["gc", "gc_fraction"],
    example="ATGC -> gc 2",
    config_fields=[
        {"name": "window", "type": "int", "description": "Bases per window."}
    ],
    **WHY,
)
FILTER = ToolRequest(
    name="between",
    node="filter",
    purpose="Keep the entities whose score is in a range.",
    port="items",
    kind="dna",
    example="0.5 in [0.4, 0.6] -> yes",
    config_fields=[
        {"name": "low", "type": "float", "description": "Lowest kept."},
        {"name": "high", "type": "float", "description": "Highest kept."},
    ],
    **WHY,
)


@pytest.fixture
def scaffold(results_dir):
    before = set(sys.modules)

    def run(req: ToolRequest, *args: str):
        (results_dir / "requests").mkdir(exist_ok=True)
        (results_dir / "requests" / f"{req.name}.json").write_text(
            req.model_dump_json()
        )
        return CliRunner().invoke(
            main, [req.name, "--out-dir", str(results_dir / "nodes"), *args]
        )

    yield run
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _load(results_dir, req: ToolRequest) -> tuple[Any, Any]:
    """Import the generated config and node under the module names they would have in src."""
    folder = "filters" if req.node == "filter" else "tools"
    pkg = f"node_dag.nodes.{folder}.{req.name}"
    sys.modules[pkg] = ModuleType(pkg)
    for name in ("config", "function"):
        path = results_dir / "nodes" / folder / req.name / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"{pkg}.{name}", path)
        assert spec and spec.loader
        sys.modules[spec.name] = module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    camel = "".join(p.title() for p in req.name.split("_"))
    return getattr(sys.modules[f"{pkg}.config"], f"{camel}Config"), getattr(
        sys.modules[f"{pkg}.function"], camel
    )


@pytest.mark.parametrize(
    ("req", "cfg"),
    [
        (TOOL, {}),
        (SCORE, {"window": 3}),
        (FILTER, {"column": "c", "low": 0.4, "high": 0.6}),
    ],
)
def test_a_scaffolded_node_has_the_contract_the_plan_was_checked_against(
    scaffold, results_dir, req, cfg
):
    result = scaffold(req)
    assert result.exit_code == 0, result.output
    config, node = _load(results_dir, req)
    made, stand_in = config(**cfg), req.stand_in()(**cfg)
    assert config.contract() == stand_in.contract()
    assert (
        made.config_hash == stand_in.config_hash
    )  # So a filter on its score column still finds it.
    with pytest.raises(NotImplementedError, match=req.name):
        node(made).run(
            **{req.port: [Dna(sequence="ATG")]},
            **({"values": [0.5]} if req.node == "filter" else {}),
        )


def test_a_scaffolded_config_keeps_the_requested_defaults_and_documents_its_fields(
    scaffold, results_dir
):
    scaffold(SCORE)
    config, _ = _load(results_dir, SCORE)
    doc = config.__doc__ or ""
    assert "window: Bases per window." in doc and "gc_fraction:" in doc
    scaffold(TOOL)
    tool, _ = _load(results_dir, TOOL)
    assert tool().stops == ("TAA",) and tool.version == 1


def test_it_prints_the_factory_edits_and_does_not_make_them(scaffold):
    out = scaffold(FILTER).output
    assert "from node_dag.nodes.filters.between.config import BetweenConfig" in out
    assert (
        "| BetweenConfig" in out
        and "BetweenConfig: Between," in out
        and "Resume" in out
    )


def test_it_overwrites_a_node_only_with_force(scaffold, results_dir):
    scaffold(TOOL)
    config = results_dir / "nodes" / "tools" / "trim_stops" / "config.py"
    config.write_text("# by hand\n")
    assert scaffold(TOOL).exit_code != 0 and config.read_text() == "# by hand\n"
    assert scaffold(TOOL, "--force").exit_code == 0 and "version" in config.read_text()


def test_bump_raises_the_version_or_adds_one(scaffold, results_dir):
    scaffold(TOOL)
    config = results_dir / "nodes" / "tools" / "trim_stops" / "config.py"
    assert "version 1 -> 2" in scaffold(TOOL, "--bump").output
    assert "version 2 -> 3" in scaffold(TOOL, "--bump").output
    config.write_text(
        "\n".join(
            line for line in config.read_text().splitlines() if "version" not in line
        )
    )
    assert "version 1 -> 2" in scaffold(TOOL, "--bump").output
    assert "version: ClassVar[int] = 2" in config.read_text()


def test_it_needs_a_request_that_names_the_node_and_bump_needs_the_node(
    scaffold, results_dir
):
    out = str(results_dir / "nodes")
    assert (
        "No tool request" in CliRunner().invoke(main, ["nope", "--out-dir", out]).output
    )
    assert (
        "No node called"
        in CliRunner().invoke(main, ["nope", "--bump", "--out-dir", out]).output
    )
    (results_dir / "requests" / "other.json").parent.mkdir(exist_ok=True)
    (results_dir / "requests" / "other.json").write_text(TOOL.model_dump_json())
    assert (
        "is a request for 'trim_stops'"
        in CliRunner().invoke(main, ["other", "--out-dir", out]).output
    )
