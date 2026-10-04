"""The loop's goal built from a benchmark instance: the gate's rules, in words, with no answer."""

import json

import pytest
from click.testing import CliRunner
from test_benchmark import WEIGHTS, inst

from node_dag import entrez
from node_dag.agent import Hypothesis
from node_dag.benchmark import Instance, exact_cai
from node_dag.types import Dna
from temporal.run_benchmark import goal_for, main

TGA = "ATGCTGAAAGGCTTTTGA"  # ends in TGA, which a most-frequent table would respell TAA


def claims(goal: dict) -> dict[str, str]:
    return {c["id"]: c["claim"] for c in goal["criteria"]}


def test_the_goal_is_a_loadable_hypothesis_for_the_instance():
    goal = goal_for(inst(TGA), WEIGHTS)
    hyp = Hypothesis.model_validate(goal)
    assert hyp.inputs["seqs"] == [Dna(sequence=TGA)]
    assert "codon adaptation" in hyp.goal
    assert json.dumps(dict(sorted(WEIGHTS.items())), separators=(",", ":")) in hyp.goal


def test_the_default_fixed_codons_are_named_with_their_codons():
    keep = claims(goal_for(inst(TGA), WEIGHTS))["fixed_codons_kept"]
    assert "ATG at index 0" in keep and "TGA at index 5" in keep


def test_an_explicit_immutable_set_replaces_the_default_in_the_goal():
    explicit = Instance(key="g", parent=Dna(sequence=TGA), immutable=frozenset({1}))
    goal = goal_for(explicit, WEIGHTS)
    assert "CTG at index 1" in claims(goal)["fixed_codons_kept"]
    assert "index 0" not in goal["goal"] and "index 5" not in goal["goal"]


def test_the_goal_writes_no_answer():
    instance = inst(TGA)
    text = json.dumps(goal_for(instance, WEIGHTS)).lower()
    assert exact_cai(instance, WEIGHTS).lower() not in text
    assert not {"optimum", "gap", "holdout", "held-out"} & set(text.split())


def fake_cds(n: int) -> list[dict]:
    """``n`` usable records, each a distinct short CDS."""
    stems = ["CTG", "AAA", "GGC", "TTT", "CCG", "GAA", "AGC", "GTT"]
    return [
        {
            "id": f"r{k}",
            "gene": f"g{k}",
            "sequence": "ATG" + stems[k % 8] * (2 + k % 3) + stems[(k + 3) % 8] + "TAA",
            "usable": True,
        }
        for k in range(n)
    ]


def test_emit_goals_writes_one_file_per_instance_and_records_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: fake_cds(12))
    out, ledger = tmp_path / "goals", tmp_path / "ledger"
    result = CliRunner().invoke(
        main,
        ["--instances", "4", "--holdout-fraction", "0.0", "--emit-goals", str(out)]
        + ["--ledger", str(ledger)],
    )
    assert result.exit_code == 0, result.output
    files = sorted(out.glob("goal_*.json"))
    assert len(files) == 4
    for f in files:
        goal = json.loads(f.read_text())
        assert Hypothesis.model_validate(goal).criteria[-1].id == "fixed_codons_kept"
    assert not ledger.exists()


def test_export_defaults_to_development_and_never_scores(tmp_path, monkeypatch):
    from node_dag.benchmark import split_instances
    from temporal import run_benchmark

    records = fake_cds(12)
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: records)
    monkeypatch.setattr(
        run_benchmark, "_table", lambda *args: pytest.fail("export scored")
    )
    picked = run_benchmark._pick(records, 10)
    split = split_instances(
        [Instance(key=r["gene"], parent=Dna(sequence=r["sequence"])) for r in picked]
    )
    assert split.dev and split.holdout
    out, ledger = tmp_path / "goals", tmp_path / "ledger"
    result = CliRunner().invoke(
        main, ["--emit-goals", str(out), "--ledger", str(ledger)]
    )
    assert result.exit_code == 0, result.output
    assert {p.name for p in out.iterdir()} == {f"goal_{i.key}.json" for i in split.dev}
    assert not ledger.exists()
    assert "Exporting the development side" in result.output


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--reason", "confirmation"],
        ["--reason", "confirmation", "--frozen", "[1]"],
        ["--reason", "confirmation", "--frozen", "{"],
    ],
)
def test_held_out_export_requires_reason_and_frozen_object(
    tmp_path, monkeypatch, extra
):
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: fake_cds(12))
    out, ledger = tmp_path / "goals", tmp_path / "ledger"
    result = CliRunner().invoke(
        main,
        [
            "--emit-goals",
            str(out),
            "--ledger",
            str(ledger),
            "--release-holdout",
            *extra,
        ],
    )
    assert result.exit_code != 0
    assert "Error:" in result.output
    assert not out.exists() and not ledger.exists()


def test_held_out_export_records_access_before_writing_and_no_attempt(
    tmp_path, monkeypatch
):
    from node_dag.benchmark import Ledger
    from temporal import run_benchmark

    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: fake_cds(12))
    out, ledger = tmp_path / "goals", tmp_path / "ledger"
    original = run_benchmark.write_atomic

    def write_after_access(path, data):
        assert len(Ledger(ledger).holdout_accesses()) == 1
        original(path, data)

    monkeypatch.setattr(run_benchmark, "write_atomic", write_after_access)
    result = CliRunner().invoke(
        main,
        [
            "--emit-goals",
            str(out),
            "--ledger",
            str(ledger),
            "--release-holdout",
            "--reason",
            "frozen confirmation",
            "--frozen",
            '{"strategy":"fixed"}',
        ],
    )
    assert result.exit_code == 0, result.output
    entries = list(Ledger(ledger).entries())
    assert len(entries) == 1 and entries[0]["kind"] == "holdout_release"
    assert entries[0]["frozen"] == {"strategy": "fixed"}
    assert list(out.glob("goal_*.json"))


@pytest.mark.parametrize("label", ["duplicate", "../outside", "x/../../outside"])
def test_export_refuses_duplicate_or_unsafe_keys_before_any_write(
    tmp_path, monkeypatch, label
):
    records = fake_cds(12)
    for record in records:
        record["gene"] = label
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: records)
    out = tmp_path / "goals"
    # One unsafe key, or several instances with the same safe key.
    count = "4" if label == "duplicate" else "1"
    result = CliRunner().invoke(
        main,
        ["--instances", count, "--holdout-fraction", "0", "--emit-goals", str(out)],
    )
    assert result.exit_code != 0 and "Error:" in result.output
    assert not out.exists()


def test_export_refuses_to_overwrite_existing_goals(tmp_path, monkeypatch):
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: fake_cds(12))
    args = [
        "--instances",
        "4",
        "--holdout-fraction",
        "0",
        "--emit-goals",
        str(tmp_path / "goals"),
    ]
    runner = CliRunner()
    assert runner.invoke(main, args).exit_code == 0
    before = {p.name: p.read_bytes() for p in (tmp_path / "goals").iterdir()}
    result = runner.invoke(main, args)
    assert result.exit_code != 0 and "already exist" in result.output
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "goals").iterdir()}


@pytest.mark.parametrize(
    "args", [["--instances", "0"], ["--instances", "-1"], ["--holdout-fraction", "1.1"]]
)
def test_invalid_export_bounds_fail_before_fetch(args, monkeypatch):
    monkeypatch.setattr(entrez, "fetch_cds", lambda accession: pytest.fail("fetched"))
    result = CliRunner().invoke(main, [*args, "--emit-goals", "unused"])
    assert result.exit_code == 2


@pytest.mark.parametrize("swap_stop", [False, True])
async def test_generated_goal_through_scripted_planner_and_temporal_loop(
    results_dir, swap_stop
):
    from pydantic_ai.messages import (
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import FunctionModel
    from test_loop import _drive, _fakes

    from node_dag.agent import build_agent, plan_prompt
    from node_dag.benchmark import gate, passes
    from node_dag.nodes.filters.at_least.config import AtLeastConfig
    from node_dag.nodes.filters.beats_reference.config import BeatsReferenceConfig
    from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
    from node_dag.nodes.tools.codon_optimise.config import CodonOptimiseConfig
    from node_dag.nodes.tools.constraint_check.config import ConstraintCheckConfig
    from node_dag.nodes.tools.recode_targeted.config import RecodeTargetedConfig
    from node_dag.registry import Registry
    from temporal.hypothesis.activities import resolve_plan

    instance = inst(TGA)
    hyp = Hypothesis.model_validate(goal_for(instance, WEIGHTS))
    cfg = ConstraintCheckConfig(
        reference=instance.parent, immutable=tuple(sorted(instance.fixed()))
    )
    score = CodonAdaptationConfig(codon_weights=WEIGHTS)
    registry = Registry(results_dir / "registry")
    steps = {}

    def step(key, config, source, port="sequence"):
        steps[key] = {
            "node": config.name,
            "config": config.model_dump(mode="json", exclude={"name", "config_hash"}),
            "inputs": {port: source},
            "why": key,
        }

    step("baseline", score, "seqs")
    step("recoded", CodonOptimiseConfig(codon_weights=WEIGHTS), "seqs")
    source = "recoded"
    if swap_stop:
        step(
            "swapped",
            RecodeTargetedConfig(targeted_codons=("TGA",), strategy="first"),
            source,
        )
        source = "swapped"
    step("scored", score, source)
    step(
        "improved",
        BeatsReferenceConfig(
            column=score.columns()["cai"],
            reference=instance.parent,
            scored_in="baseline",
        ),
        "scored",
        "items",
    )
    step("constraints", cfg, "improved.yes")
    # Use the registered node created through the model-facing tool below.
    steps["constraints"]["node"] = f"constraint_check__{cfg.config_hash}"
    steps["constraints"]["config"] = {}
    assertions = [
        {
            "criterion": c,
            "step": "improved",
            "branch": "yes",
            "claim": claims(goal_for(instance, WEIGHTS))[c],
        }
        for c in ("higher_cai", "only_improved_kept")
    ]
    source = "constraints"
    for criterion, column in [
        ("protein_preserved", "protein_unchanged"),
        ("length_preserved", "length_unchanged"),
        ("fixed_codons_kept", "immutable_unchanged"),
    ]:
        step(
            criterion,
            AtLeastConfig(column=cfg.columns()[column], threshold=1),
            source,
            "items",
        )
        assertions.append(
            {
                "criterion": criterion,
                "step": criterion,
                "branch": "yes",
                "claim": claims(goal_for(instance, WEIGHTS))[criterion],
            }
        )
        source = f"{criterion}.yes"
    plan = {
        "hypothesis": "Improve CAI and measure every constraint",
        "expected": "Improved DNA with all fixed codons retained",
        "inputs": hyp.input_kinds(),
        "steps": steps,
        "assertions": assertions,
    }
    seen = []

    def script(messages, info):
        for message in messages:
            seen.extend(
                p
                for p in message.parts
                if isinstance(p, (RetryPromptPart, ToolReturnPart))
            )
        turn = sum(isinstance(m, ModelResponse) for m in messages)
        if turn == 0:
            name, args = "describe_node", {"name": "constraint_check"}
        elif turn in (1, 2):
            config = cfg.model_dump(mode="json", exclude={"config_hash"})
            if turn == 1:
                config["immutable"] = [-1]
            name, args = (
                "create_node",
                {"config": config, "description": "Check the goal's fixed codons"},
            )
        else:
            name, args = info.output_tools[0].name, dict(plan)
            if turn == 3:
                args["assertions"] = assertions[:-1]
        return ModelResponse(parts=[ToolCallPart(name, args)])

    agent = build_agent(FunctionModel(script), registry)
    planned = (await agent.run(plan_prompt(hyp), deps=hyp)).output
    assert any(
        isinstance(p, RetryPromptPart) and "immutable" in str(p.content) for p in seen
    )
    assert any(
        isinstance(p, RetryPromptPart) and "No assertion covers" in str(p.content)
        for p in seen
    )
    assert any(
        isinstance(p, ToolReturnPart) and "immutable_unchanged" in str(p.content)
        for p in seen
    )
    resolved = resolve_plan(planned)
    assert resolved.dag is not None
    # A permissive scripted verifier cannot overrule a failed executable assertion.
    done = await _drive(
        _fakes({}, [planned], [resolved], [True]), hyp=hyp, max_rounds=1
    )
    assert done.state == ("not achieved" if swap_stop else "achieved")
    assert done.current and done.current.outcome
    outcome = done.current.outcome
    candidates = outcome.values["improved.yes"].items
    assert candidates
    assert all(passes(gate(instance, c.sequence)) is not swap_stop for c in candidates)
    assert done.current.held["fixed_codons_kept.yes"] is not swap_stop
    if swap_stop:
        assert all(c.sequence.endswith("TAA") for c in candidates)
        assert not outcome.values["fixed_codons_kept.yes"].items
    else:
        assert outcome.values["fixed_codons_kept.yes"].items
