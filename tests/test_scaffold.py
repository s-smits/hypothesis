"""The scaffolder writes a node package that imports, declares the requested contract
and refuses to pretend ``run`` is written.

Every test scaffolds under ``tmp_path``, never into ``src/``, and loads the result by
file location under the module name it would have had, so ``function.py``'s import of
its own ``config`` resolves without the package ever being on the path.
"""

import importlib.util
import json
import sys
from types import ModuleType

import pytest
from click.testing import CliRunner

from node_dag.plan import ToolRequest
from node_dag.wiring import PortContract
from temporal.scaffold_node import main

TOOL = {
    "name": "trim_stops",
    "node": "tool",
    "purpose": "Remove every codon after the first in-frame stop codon.",
    "category": "generation",
    "inputs": {"sequence": "dna"},
    "output": "dna",
    "config_fields": [
        {
            "name": "stops",
            "type": "list[str]",
            "description": "The codons counted as stops.",
        },
        {
            "name": "keep_stop",
            "type": "bool",
            "description": "Whether to keep the stop codon itself.",
            "required": False,
            "default": True,
        },
    ],
    "why_needed": "No existing node can cut a sequence at its first stop codon.",
    "why_not_composable": "codons_absent only tests, and recode_codons only swaps.",
    "example": "sequence=ATGTCGTAAGCT with stops=('TAA',) -> ATGTCGTAA",
}

DECISION = {
    "name": "gc_within",
    "node": "decision",
    "purpose": "Decide whether a sequence's GC content sits inside a range.",
    "category": "filter",
    "inputs": {"sequence": "dna", "reference": "dna"},
    "forwards": "sequence",
    "config_fields": [
        {
            "name": "low",
            "type": "float",
            "description": "The lowest acceptable GC fraction.",
        }
    ],
    "why_needed": "Nothing can check GC content, which synthesis houses reject on.",
    "why_not_composable": "dna_atom_score counts atoms and at_least reads only scores.",
    "example": "sequence=ATGGCGTAA with low=0.4 -> yes, forwarding on <step>.yes",
}


@pytest.fixture
def loaded():
    """Undo whatever a test added to ``sys.modules`` for its generated package."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def scaffold(results_dir, payload, *args):
    """Write ``payload`` as a request, scaffold it under ``tmp_path``, return the result."""
    requests = results_dir / "requests"
    requests.mkdir(exist_ok=True)
    (requests / f"{payload['name']}.json").write_text(json.dumps(payload))
    out = results_dir / "nodes"
    return CliRunner().invoke(main, [payload["name"], "--out-dir", str(out), *args])


def package_dir(results_dir, payload):
    """Where ``scaffold`` put the generated package."""
    kind = "tools" if payload["node"] == "tool" else "decisions"
    return results_dir / "nodes" / kind / payload["name"]


def load(results_dir, payload, module):
    """Import ``config`` or ``function`` from the generated package."""
    kind = "tools" if payload["node"] == "tool" else "decisions"
    pkg = f"node_dag.nodes.{kind}.{payload['name']}"
    directory = package_dir(results_dir, payload)
    sys.modules.setdefault(pkg, ModuleType(pkg))
    for name in ("config", "function"):
        full = f"{pkg}.{name}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full, directory / f"{name}.py")
        loaded_module = importlib.util.module_from_spec(spec)
        sys.modules[full] = loaded_module
        spec.loader.exec_module(loaded_module)
    return sys.modules[f"{pkg}.{module}"]


def test_a_scaffolded_tool_declares_the_contract_that_was_requested(
    results_dir, loaded
):
    result = scaffold(results_dir, TOOL)
    assert result.exit_code == 0, result.output
    config = load(results_dir, TOOL, "config").TrimStopsConfig

    contract = PortContract.of(config)
    assert contract.inputs == TOOL["inputs"]
    assert contract.output == TOOL["output"]
    assert contract.forwards is None
    assert config.contract()["inputs"] == {"sequence": "dna"}
    assert config.contract()["outputs"] == {"<step>": "dna"}


def test_a_scaffolded_config_carries_a_bumpable_cache_version(results_dir, loaded):
    scaffold(results_dir, TOOL)
    config = load(results_dir, TOOL, "config").TrimStopsConfig
    assert config.version == 1
    assert config.example == TOOL["example"]  # test_config_declares_what_run_takes
    source = (package_dir(results_dir, TOOL) / "config.py").read_text()
    assert "version: ClassVar[int] = 1" in source
    assert "Part of the node result cache key" in source
    assert "change to run()" in source


def test_a_scaffolded_config_takes_the_requested_fields(results_dir, loaded):
    scaffold(results_dir, TOOL)
    config = load(results_dir, TOOL, "config").TrimStopsConfig
    built = config(stops=("TAA",))
    assert built.stops == ("TAA",)
    assert built.keep_stop is True  # the request's default, not a required field
    assert "The codons counted as stops." in config.__doc__


def test_a_scaffolded_decision_forwards_and_has_no_output(results_dir, loaded):
    result = scaffold(results_dir, DECISION)
    assert result.exit_code == 0, result.output
    config = load(results_dir, DECISION, "config").GcWithinConfig

    assert config.forwards == "sequence"
    assert not hasattr(config, "output")
    contract = PortContract.of(config)
    assert contract.output is None
    assert contract.forwards == "sequence"
    assert contract.inputs == DECISION["inputs"]
    assert config.contract()["outputs"] == {"<step>.yes": "dna", "<step>.no": "dna"}


def test_a_scaffolded_run_refuses_to_pretend_it_is_written(results_dir, loaded):
    scaffold(results_dir, TOOL)
    config = load(results_dir, TOOL, "config").TrimStopsConfig
    node = load(results_dir, TOOL, "function").TrimStops(config(stops=("TAA",)))

    from node_dag.types import Dna

    with pytest.raises(NotImplementedError) as raised:
        node.run(sequence=Dna(sequence="ATGTAA"))
    assert "trim_stops" in str(raised.value)
    assert "sequence (dna)" in str(raised.value)


def test_scaffolding_prints_the_factory_edits_without_applying_them(results_dir):
    result = scaffold(results_dir, DECISION)
    assert "from node_dag.nodes.decisions.gc_within.config import GcWithinConfig" in (
        result.output
    )
    assert "from node_dag.nodes.decisions.gc_within.function import GcWithin" in (
        result.output
    )
    assert "| GcWithinConfig" in result.output
    assert "GcWithinConfig: GcWithin," in result.output
    assert "restart the worker" in result.output
    assert "resume the hypothesis blocked on this node" in result.output


def test_bump_raises_the_cache_version(results_dir):
    scaffold(results_dir, TOOL)
    config = package_dir(results_dir, TOOL) / "config.py"

    result = scaffold(results_dir, TOOL, "--bump")
    assert result.exit_code == 0, result.output
    assert "version 1 -> 2" in result.output
    assert "version: ClassVar[int] = 2" in config.read_text()

    scaffold(results_dir, TOOL, "--bump")
    assert "version: ClassVar[int] = 3" in config.read_text()


def test_bump_adds_a_version_to_a_config_that_never_declared_one(results_dir):
    scaffold(results_dir, TOOL)
    config = package_dir(results_dir, TOOL) / "config.py"
    lines = [
        line
        for line in config.read_text().splitlines()
        if "version" not in line and "cache key" not in line and "run()" not in line
    ]
    config.write_text("\n".join(lines) + "\n")

    result = scaffold(results_dir, TOOL, "--bump")
    assert result.exit_code == 0, result.output
    assert "version 1 -> 2" in result.output
    assert "version: ClassVar[int] = 2" in config.read_text()


def test_bump_needs_a_node_that_is_already_there(results_dir):
    result = scaffold(results_dir, TOOL, "--bump")
    assert result.exit_code != 0
    assert "No node called 'trim_stops'" in result.output


def test_it_refuses_to_overwrite_a_node_without_force(results_dir):
    scaffold(results_dir, TOOL)
    config = package_dir(results_dir, TOOL) / "config.py"
    config.write_text("# written by hand\n")

    result = scaffold(results_dir, TOOL)
    assert result.exit_code != 0
    assert "--force" in result.output
    assert config.read_text() == "# written by hand\n"

    result = scaffold(results_dir, TOOL, "--force")
    assert result.exit_code == 0, result.output
    assert "version: ClassVar[int] = 1" in config.read_text()


def test_it_needs_a_request_file_naming_the_node_it_asks_for(results_dir):
    out = results_dir / "nodes"
    result = CliRunner().invoke(main, ["nope", "--out-dir", str(out)])
    assert result.exit_code != 0
    assert "No tool request at" in result.output

    payload = {**TOOL, "name": "trim_stops"}
    requests = results_dir / "requests"
    requests.mkdir(exist_ok=True)
    (requests / "other_name.json").write_text(json.dumps(payload))
    result = CliRunner().invoke(main, ["other_name", "--out-dir", str(out)])
    assert result.exit_code != 0
    assert "is a request for 'trim_stops'" in result.output


def test_every_example_request_shape_round_trips(results_dir):
    """The scaffolder's input is exactly a ToolRequest, not a looser shape."""
    assert ToolRequest.model_validate(TOOL).node == "tool"
    assert ToolRequest.model_validate(DECISION).forwards == "sequence"
