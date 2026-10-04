"""Compare recoding strategies on real genes and append the attempt to the ledger.

It sits with the other entry points for discoverability but runs nothing on Temporal:
the comparison is deterministic and local, so there is no workflow, worker or server
involved, and it reaches no network: the genes come from a committed set, so the same
command gives the same comparison. ``--genes`` runs it on another set.

Development instances are scored by default. The held-out instances are reached only
with ``--release-holdout``, which requires a reason and what was frozen beforehand, and
which records the access in the ledger before any held-out sequence is read.
"""

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import click

from node_dag import genes
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
    pair_weights_from,
    random_synonymous,
    record_attempt,
    split_instances,
)
from node_dag.benchmark import (
    release_holdout as release_holdout_fn,
)
from node_dag.dna import codons
from node_dag.types import Dna

FLOOR = math.log(0.1)


def _pick(records: list[dict], n: int) -> list[dict]:
    """``n`` records spread across the length range, chosen deterministically."""
    by_length = sorted(records, key=lambda r: (len(r["sequence"]), r["id"]))
    if n >= len(by_length):
        return by_length
    step = len(by_length) // n
    return [by_length[i * step] for i in range(n)]


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
        present = [r.gap_closed for r in out[name] if r.gap_closed is not None]
        click.echo(
            f"{(sum(present) / len(present)):>13.1%} " if present else "          n/a ",
            nl=False,
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


@click.command()
@click.option(
    "--genes",
    "genes_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Gene set JSON to compare on. Default: the committed E. coli K-12 set.",
)
@click.option(
    "--instances", "n_instances", default=10, help="How many genes to compare on."
)
@click.option("--seed", default=11, help="Seeds the random synonymous baselines.")
@click.option("--draws", default=8, help="How many draws the best-of-N baseline gets.")
@click.option(
    "--holdout-fraction",
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
    "--ledger",
    "ledger_root",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to append the attempt. Default $NODE_DAG_RESULTS/ledger.",
)
def main(
    genes_path: Path | None,
    n_instances: int,
    seed: int,
    draws: int,
    holdout_fraction: float,
    salt: str,
    release_holdout: bool,
    reason: str,
    frozen: str,
    ledger_root: Path | None,
) -> None:
    """Compare random, best-of-N, greedy and exact recoding on a committed gene set."""
    release, frozen_raw = release_holdout, frozen

    try:
        gene_set = genes.load(genes_path)
    except genes.GeneSetError as e:
        raise click.ClickException(
            f"{e}. The comparison needs a reference set; nothing was recorded."
        ) from e
    records = gene_set.cds
    where = gene_set.provenance.get("accession", str(gene_set.path))
    if len(records) <= n_instances:
        raise click.ClickException(
            f"{where} has {len(records)} CDS, too few to both compare on "
            f"{n_instances} and derive weights from the rest."
        )

    picked = _pick(records, n_instances)
    picked_ids = {r["id"] for r in picked}
    reference = [r["sequence"] for r in records if r["id"] not in picked_ids]
    click.echo(
        f"{len(records)} CDS from {where}: {len(picked)} for the comparison, "
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
        f"{len(split.holdout)} held out. Scoring the {which} side.\n"
    )

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

    for objective, out, weights in (
        (pair_obj, pair_out, pair_w),
        (cai_obj, cai_out, cai_w),
    ):
        path = record_attempt(
            led,
            manifest_=manifest(
                instances,
                objective,
                weights,
                seed=seed,
                notes=(
                    f"weights from {where} ({gene_set.path.name}, sequences sha256 "
                    f"{gene_set.provenance.get('sha256', 'unrecorded')[:16]}), "
                    f"{len(reference)} reference genes, comparison genes excluded; "
                    f"pair floor ln(0.1); scored the {which} side"
                ),
                split=split,
            ),
            split=split,
            results=out,
            selection="none: fixed strategies, no selection was made",
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
