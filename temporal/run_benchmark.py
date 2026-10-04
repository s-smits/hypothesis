"""Compare recoding strategies on real genes and append the attempt to the ledger.

It sits with the other entry points for discoverability but runs nothing on Temporal:
the comparison is deterministic and local, so there is no workflow, worker or server
involved. It does reach NCBI through ``node_dag.entrez``, which caches under
``$NODE_DAG_RESULTS/entrez``, so only the first run needs the network.

Development instances are scored by default. The held-out instances are reached only
with ``--release-holdout``, which requires a reason and what was frozen beforehand, and
which records the access in the ledger before any held-out sequence is read.
"""

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import click

from node_dag import entrez
from node_dag.agent import Hypothesis
from node_dag.benchmark import (
    Instance,
    Ledger,
    Objective,
    Result,
    cai_objective,
    cai_weights_from,
    codon_pair_objective,
    compare,
    exact_cai,
    exact_codon_pair,
    greedy_chain,
    manifest,
    mean_gap_closed,
    pair_weights_from,
    random_synonymous,
    record_attempt,
    split_instances,
)
from node_dag.benchmark import (
    release_holdout as release_holdout_fn,
)
from node_dag.dna import codons
from node_dag.storage import write_atomic
from node_dag.types import Dna

FLOOR = math.log(0.1)


def _pick(records: list[dict], n: int) -> list[dict]:
    """``n`` records spread across the length range, chosen deterministically."""
    by_length = sorted(records, key=lambda r: (len(r["sequence"]), r["id"]))
    if n >= len(by_length):
        return by_length
    step = len(by_length) // n
    return [by_length[i * step] for i in range(n)]


def goal_for(instance: Instance, weights: Mapping[str, float]) -> dict:
    """The loop's goal for one instance, in the words of what the gate checks.

    The sequence and weight table are the benchmark's own, and the codons that must not
    change come from ``Instance.fixed``, so the loop is asked what the gate scores. The
    optimum, a gap and the held-out split are never written. The dict is a
    ``Hypothesis`` without an id, which the loop fills in.
    """
    table = json.dumps(dict(sorted(weights.items())), separators=(",", ":"))
    cs = codons(instance.parent.sequence)
    fixed = sorted(instance.fixed())
    keep = ", ".join(f"{cs[i]} at index {i}" for i in fixed)
    claims = {
        "protein_preserved": "Every kept output DNA sequence translates to exactly the same protein as the input sequence: only synonymous codon changes were made.",
        "higher_cai": "Every kept output sequence has a codon adaptation index, scored with the given weight table, strictly higher than that of the first input sequence.",
        "only_improved_kept": "Any sequence whose codon adaptation index is not higher than the first sequence's is excluded from the output.",
        "length_preserved": "Every kept output sequence has the same length as the input sequence.",
        "fixed_codons_kept": f"Every kept output sequence keeps the input's codon at each fixed position ({keep}; codon indices count from 0). A synonymous codon there is a change.",
    }
    return {
        "goal": (
            "Raise the codon adaptation index of the DNA sequence without changing its protein. "
            f"Score it with this codon weight table: {table} . "
            f"Leave these codons as they are: {keep}. "
            f"Check them with constraint_check using the input as reference and immutable={fixed}; "
            "filter immutable_unchanged at 1. "
            "Keep the ones that score higher than the first sequence."
        ),
        "inputs": {"seqs": [{"kind": "dna", "sequence": instance.parent.sequence}]},
        "criteria": [
            {"id": i, "claim": c, "source": "human"} for i, c in claims.items()
        ],
    }


def _table(
    title: str,
    instances: Sequence[Instance],
    objective: Objective,
    strategies: Mapping[str, Callable[[Instance], list[str]]],
) -> dict[str, list[Result]]:
    out = compare(instances, objective, strategies)
    names = list(strategies)
    click.echo("=" * 78)
    click.echo(title)
    click.echo("=" * 78)
    click.echo(f"{'gene':<12}{'codons':>7}  " + "".join(f"{n:>14}" for n in names))
    for idx, inst in enumerate(instances):
        cells = []
        for name in names:
            gap = out[name][idx].gap_closed
            cells.append("   gate-fail  " if gap is None else f"{gap:>13.1%} ")
        click.echo(
            f"{inst.key:<12}{len(codons(inst.parent.sequence)):>7}  " + "".join(cells)
        )
    click.echo("-" * 78)
    click.echo(f"{'mean':<12}{'':>7}  ", nl=False)
    for name in names:
        mean = mean_gap_closed(out[name])
        click.echo(
            f"{mean:>13.1%} " if mean is not None else "          n/a ", nl=False
        )
    click.echo("")
    click.echo(f"{'passed':<12}{'':>7}  ", nl=False)
    for name in names:
        click.echo(
            f"{sum(r.passed for r in out[name]):>10}/{len(instances):<3}", nl=False
        )
    click.echo("")
    click.echo(f"{'evals':<12}{'':>7}  ", nl=False)
    for name in names:
        click.echo(f"{sum(r.evaluations for r in out[name]):>13} ", nl=False)
    click.echo("\n")
    return out


def loop_runs(
    results: Path, instances: Sequence[Instance], goal: str
) -> tuple[Callable[[Instance], list[str]], dict[str, int], list[str], str]:
    """Saved loop runs (``results/hypotheses/*.json``) as a strategy, with its cost.

    A run counts for the one instance whose sequence is among its inputs, when its goal
    contains ``goal``. It proposes the smallest non-empty ``.yes`` table of the last
    round its verifier accepted; with none, the gene is a failed row, not dropped. The
    budget sums every counted run's rounds and tokens, accepted or not. Returns the
    strategy, the budget, notes on files not counted, and the selection rule.
    """
    by_sequence = {i.parent.sequence: i.key for i in instances}
    kept: dict[str, list[str]] = {}
    rounds = tokens = 0
    notes: list[str] = []
    for path in sorted((results / "hypotheses").glob("*.json")):
        try:
            run = Hypothesis.model_validate_json(path.read_text())
        except ValueError as e:
            notes.append(f"{path.name}: unreadable, not counted ({type(e).__name__})")
            continue
        genes = {
            by_sequence[i.sequence]
            for v in run.inputs.values()
            for i in v
            if isinstance(i, Dna) and i.sequence in by_sequence
        }
        if goal not in run.goal.lower() or len(genes) != 1:
            notes.append(f"{path.name}: goal or input does not match, not counted")
            continue
        (gene,) = genes
        if gene in kept:
            raise click.ClickException(f"Two loop runs for {gene}; pass one per gene.")
        won = [
            a.outcome
            for a in run.attempts
            if a.verdict and a.verdict.achieved and a.outcome
        ]
        tables = [
            [i.sequence for i in t.items if isinstance(i, Dna)]
            for k, t in (won[-1].values if won else {}).items()
            if k.endswith(".yes")
        ]
        kept[gene] = min((t for t in tables if t), key=len, default=[])
        rounds += len(run.attempts)
        tokens += run.usage.get("total", 0)
    counts = {i.key: len(kept.get(i.key, [])) for i in instances}
    selection = (
        "fixed strategies: none. loop: the smallest non-empty filter .yes table of the "
        "last round its verifier accepted, then the best of those by this objective; "
        "the loop has no answer step, so that choice is the harness's and optimistic. "
        f"No accepted round is a failed row. Kept per gene: {counts}"
    )
    return (
        lambda i: kept.get(i.key, []),
        {"rounds": rounds, "tokens": tokens},
        notes,
        selection,
    )


def loop_line(rows: Sequence[Result]) -> str:
    """Passed, the mean over every instance, and the mean over those with a gap."""
    gaps = [r.gap_closed for r in rows if r.gap_closed is not None]
    whole = sum(r.gap_closed or 0.0 for r in rows if r.passed) / len(rows)
    part = f"{sum(gaps) / len(gaps):.1%}" if gaps else "n/a"
    return (
        f"loop: passed {sum(r.passed for r in rows)}/{len(rows)}; mean gap closed "
        f"{whole:.1%} over all {len(rows)} genes, {part} over the {len(gaps)} with a gap"
    )


@click.command()
@click.option(
    "--accession",
    default="NC_000913.3",
    help="NCBI record to take coding sequences from.",
)
@click.option(
    "--instances",
    "n_instances",
    type=click.IntRange(min=1),
    default=10,
    help="How many genes to compare on.",
)
@click.option("--seed", default=11, help="Seeds the random synonymous baselines.")
@click.option("--draws", default=8, help="How many draws the best-of-N baseline gets.")
@click.option(
    "--holdout-fraction",
    type=click.FloatRange(0, 1),
    default=0.3,
    help="Share of instances reserved for confirmation.",
)
@click.option(
    "--salt", default="", help="Changes the split. A new salt is a new split."
)
@click.option(
    "--release-holdout",
    is_flag=True,
    help="Score the held-out instances instead of the development ones.",
)
@click.option("--reason", default="", help="Why the held-out set is being read.")
@click.option(
    "--frozen",
    default="",
    help='JSON of what was fixed before looking, e.g. \'{"strategy": "exact_dp"}\'.',
)
@click.option(
    "--loop-results",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="A loop results dir: score its codon adaptation runs as strategy 'loop'.",
)
@click.option(
    "--ledger",
    "ledger_root",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to append the attempt. Default $NODE_DAG_RESULTS/ledger.",
)
@click.option(
    "--emit-goals",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Write one loop goal file per instance to this directory, then stop: no scores or attempts; held-out access is still recorded.",
)
def main(
    accession: str,
    n_instances: int,
    seed: int,
    draws: int,
    holdout_fraction: float,
    salt: str,
    release_holdout: bool,
    reason: str,
    frozen: str,
    loop_results: Path | None,
    ledger_root: Path | None,
    emit_goals: Path | None,
) -> None:
    """Compare random, best-of-N, greedy and exact recoding on genes from ACCESSION."""
    release, frozen_raw = release_holdout, frozen

    try:
        records = [r for r in entrez.fetch_cds(accession) if r.get("usable")]
    except entrez.EntrezError as e:
        raise click.ClickException(
            f"Could not fetch {accession} from NCBI: {e}. The comparison needs a "
            "reference set; nothing was recorded."
        ) from e
    if len(records) <= n_instances:
        raise click.ClickException(
            f"{accession} has {len(records)} usable CDS, too few to both compare on "
            f"{n_instances} and derive weights from the rest."
        )

    picked = _pick(records, n_instances)
    picked_ids = {r["id"] for r in picked}
    reference = [r["sequence"] for r in records if r["id"] not in picked_ids]
    click.echo(
        f"{len(records)} usable CDS from {accession}: {len(picked)} for the comparison, "
        f"{len(reference)} for the weight tables"
    )

    cai_w = cai_weights_from(reference)
    pair_w = pair_weights_from(reference, FLOOR)
    all_instances = [
        Instance(key=r["gene"] or r["id"], parent=Dna(sequence=r["sequence"]))
        for r in picked
    ]
    split = split_instances(all_instances, holdout_fraction=holdout_fraction, salt=salt)
    led = Ledger(Path(ledger_root) if ledger_root else None)

    if release:
        try:
            parsed = json.loads(frozen_raw) if frozen_raw else {}
            if not isinstance(parsed, dict):
                raise click.ClickException("--frozen must be a JSON object")
        except json.JSONDecodeError as e:
            raise click.ClickException(f"--frozen is not valid JSON: {e}") from e
        try:
            instances = list(
                release_holdout_fn(split, reason=reason, frozen=parsed, ledger=led)
            )
        except ValueError as e:
            raise click.ClickException(str(e)) from e
        which = "held-out"
    else:
        instances = list(split.dev)
        which = "development"

    if not instances:
        raise click.ClickException(
            f"The {which} side of this split is empty. Adjust --holdout-fraction."
        )
    click.echo(
        f"split {split.split_hash}: {len(split.dev)} development, "
        f"{len(split.holdout)} held out. "
        f"{'Exporting' if emit_goals else 'Scoring'} the {which} side.\n"
    )
    if emit_goals:
        keys = [i.key for i in instances]
        if len(set(keys)) != len(keys):
            raise click.ClickException(
                "Duplicate instance keys would overwrite goal files"
            )
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", key) for key in keys):
            raise click.ClickException(
                "Instance keys must be safe filename labels: letters, digits, _, . or -"
            )
        paths = [emit_goals / f"goal_{key}.json" for key in keys]
        if any(path.exists() for path in paths):
            raise click.ClickException(
                "Goal files already exist; use a fresh export directory"
            )
        for i, path in zip(instances, paths):
            write_atomic(path, json.dumps(goal_for(i, cai_w), indent=2).encode())
        click.echo(f"wrote {len(instances)} goals to {emit_goals}; nothing was scored")
        return

    pair_strategies = {
        "original": lambda i: [i.parent.sequence],
        "random": lambda i: [random_synonymous(i, seed=seed)],
        f"best_of_{draws}": lambda i: [
            random_synonymous(i, seed=seed, draw=d) for d in range(draws)
        ],
        "greedy": lambda i: [greedy_chain(i, pair_w, FLOOR)],
        "exact_dp": lambda i: [exact_codon_pair(i, pair_w, FLOOR)],
    }
    cai_strategies = {
        "original": lambda i: [i.parent.sequence],
        "random": lambda i: [random_synonymous(i, seed=seed)],
        f"best_of_{draws}": lambda i: [
            random_synonymous(i, seed=seed, draw=d) for d in range(draws)
        ],
        "greedy_exact": lambda i: [exact_cai(i, cai_w)],
    }

    cai_note = fixed = ("none: fixed strategies, no selection was made", None, [])
    if loop_results:
        propose, spent, skipped, how = loop_runs(
            loop_results, instances, "codon adaptation"
        )
        cai_strategies = {**cai_strategies, "loop": propose}
        cai_note = (how, {"loop": spent}, skipped)

    pair_obj = codon_pair_objective(pair_w, FLOOR)
    cai_obj = cai_objective(cai_w)
    pair_out = _table(
        "Objective: codon pair, log observed over expected (maximise)",
        instances,
        pair_obj,
        pair_strategies,
    )
    cai_out = _table(
        "Objective: CAI, relative adaptiveness (maximise). Separable, so greedy is exact.",
        instances,
        cai_obj,
        cai_strategies,
    )
    if loop_results:
        click.echo(loop_line(cai_out["loop"]) + "\n")

    for objective, out, weights in (
        (pair_obj, pair_out, pair_w),
        (cai_obj, cai_out, cai_w),
    ):
        selection, budget, errors = cai_note if objective is cai_obj else fixed
        path = record_attempt(
            led,
            manifest_=manifest(
                instances,
                objective,
                weights,
                seed=seed,
                notes=(
                    f"weights from {accession}, {len(reference)} reference genes, "
                    f"comparison genes excluded; pair floor ln(0.1); "
                    f"scored the {which} side"
                ),
                split=split,
            ),
            split=split,
            results=out,
            selection=selection,
            budget=budget,
            errors=errors,
        )
        click.echo(f"recorded {objective.name}: {path}")

    accesses = led.holdout_accesses()
    if accesses:
        click.echo(
            f"\n{len(accesses)} held-out access(es) recorded in this ledger. "
            "A confirmation claim should rest on exactly one, after freezing."
        )


if __name__ == "__main__":
    main()
