"""A deterministic harness for comparing recoding strategies on fixed instances.

The first question a benchmark has to answer is about itself: does the evaluator
separate strategies whose ordering is already known? Until it does, no result from a
composed DAG or a model means anything. So this compares random synonymous recoding,
a myopic greedy pass and the exact optimum, with no model involved.

The exact optimum is computable here, which is unusual and worth exploiting. A codon
adaptation index is a geometric mean of independent per-codon terms, so choosing the
best synonym at each codon separately is optimal. A codon pair score is a mean over
adjacent pairs, so the choices form a chain and dynamic programming over it is optimal.
That gives an absolute denominator: a strategy's score can be reported as a fraction of
what is attainable, rather than only as better or worse than a baseline. It also makes
a null result legible, since reaching 70% of an optimum that DP finds in milliseconds
is a specific statement.

What it does not do: it chooses no weight tables, which have no provenance in this
repository yet, so a caller supplies them and the manifest records their hash. It holds
nothing back, so it supports no claim about generalisation; a held-out split is separate
work. And every objective here is a sequence statistic, so none of it speaks to
expression in a cell, fitness or viability.
"""

import hashlib
import json
import math
import os
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from itertools import pairwise
from pathlib import Path
from typing import TypedDict

from node_dag.dna import CODON_TABLE, SYNONYMS, codons
from node_dag.lineage import Lineage, Outcome, Variant, outcomes
from node_dag.nodes.tools.constraint_check.config import ConstraintCheckConfig
from node_dag.nodes.tools.constraint_check.function import ConstraintCheck
from node_dag.storage import write_atomic
from node_dag.types import Dna

__all__ = [
    "GoalSpec",
    "Instance",
    "Ledger",
    "Objective",
    "Result",
    "Split",
    "cai_objective",
    "cai_weights_from",
    "codon_pair_objective",
    "compare",
    "dependency_versions",
    "exact_cai",
    "exact_codon_pair",
    "goal_for",
    "greedy_chain",
    "ledger_dir",
    "manifest",
    "pair_weights_from",
    "random_synonymous",
    "record_attempt",
    "release_holdout",
    "run_strategy",
    "split_instances",
]


@dataclass(frozen=True)
class Instance:
    """One gene to recode, and what may not be touched.

    Args:
        key: How this instance is named in every report. It is not derived from the
            sequence, so two genes that recode to the same string stay distinct.
        parent: The original coding sequence.
        immutable: Codon indices that must keep their codon, whatever the objective.
            The start and the stop codon by default, since recoding either changes
            what the sequence is rather than how it is written.
    """

    key: str
    parent: Dna
    immutable: frozenset[int] = field(default_factory=frozenset)

    def fixed(self) -> frozenset[int]:
        """The codon indices this instance keeps, its own or the default.

        One place decides it, so the search space, the gate and the manifest cannot
        disagree about which positions were off limits.
        """
        return self.immutable or default_immutable(self.parent.sequence)

    def choices(self) -> list[tuple[str, ...]]:
        """The codons allowed at each position, in order.

        An immutable position, a codon with no synonym and a trailing partial codon
        all give a single choice, so a caller does not special-case them.
        """
        cs = codons(self.parent.sequence)
        fixed = self.fixed()
        return [
            (c,) if i in fixed or len(c) != 3 else tuple(SYNONYMS[CODON_TABLE[c]])
            for i, c in enumerate(cs)
        ]


def default_immutable(sequence: str) -> frozenset[int]:
    """The first and last codon indices, which is what a task spec usually fixes."""
    n = len(codons(sequence))
    return frozenset({0, n - 1}) if n else frozenset()


@dataclass(frozen=True)
class Objective:
    """A score to maximise, with the exact optimiser that matches it.

    Args:
        name: How the objective is named in reports.
        score: The score of a sequence. It must agree with the node that computes the
            same quantity, so the harness measures what a DAG would.
        optimum: The best sequence allowed by an instance's choices. It must be exact,
            since the whole point is an absolute denominator.
    """

    name: str
    score: Callable[[str], float]
    optimum: Callable[[Instance], str]


# --- exact optimisers --------------------------------------------------------


def exact_cai(instance: Instance, weights: Mapping[str, float]) -> str:
    """The sequence maximising CAI, by choosing each codon independently.

    A CAI is the geometric mean of each codon's weight over the best weight for its
    amino acid. The terms multiply and each depends on one codon, so maximising them
    one at a time is optimal rather than merely good.
    """
    return "".join(
        max(choice, key=lambda c: (weights.get(c, 0.0), c))
        for choice in instance.choices()
    )


def exact_codon_pair(
    instance: Instance, pair_weights: Mapping[str, float], missing: float = 0.0
) -> str:
    """The sequence maximising the mean adjacent-pair weight, by dynamic programming.

    Only neighbouring codons interact, so the choices form a chain and the optimum
    follows from one pass forward and a backtrack. Greedy is not optimal here, which
    is what makes this objective worth optimising at all.
    """
    choices = instance.choices()
    if not choices:
        return ""
    # best[c] is the greatest pair-weight total for a prefix ending in codon c.
    best: dict[str, float] = {c: 0.0 for c in choices[0]}
    back: list[dict[str, str]] = []
    for prev_choice, this_choice in pairwise(choices):
        nxt: dict[str, float] = {}
        step: dict[str, str] = {}
        for c in this_choice:
            # Ties break on the previous codon's own text, so the result is stable.
            p = max(
                prev_choice,
                key=lambda q: (best[q] + pair_weights.get(q + c, missing), q),
            )
            nxt[c] = best[p] + pair_weights.get(p + c, missing)
            step[c] = p
        back.append(step)
        best = nxt
    last = max(best, key=lambda c: (best[c], c))
    out = [last]
    for step in reversed(back):
        out.append(step[out[-1]])
    return "".join(reversed(out))


def greedy_chain(
    instance: Instance, pair_weights: Mapping[str, float], missing: float = 0.0
) -> str:
    """Pick each codon for its pair with the codon already chosen, left to right.

    A myopic baseline for the pair objective, not an optimiser: it commits to a codon
    before seeing what the next position would have preferred. Its gap to
    ``exact_codon_pair`` is the part of the objective that needs search.
    """
    choices = instance.choices()
    if not choices:
        return ""
    out = [max(choices[0], key=lambda c: c)]
    for choice in choices[1:]:
        prev = out[-1]
        out.append(max(choice, key=lambda c: (pair_weights.get(prev + c, missing), c)))
    return "".join(out)


def random_synonymous(instance: Instance, seed: int, draw: int = 0) -> str:
    """One uniform synonymous redraw of every mutable codon.

    Seeded by the instance key as well as the seed and draw, so an instance's draw
    does not depend on how many other instances are in the batch.
    """
    import random

    rng = random.Random(f"{seed}:{draw}:{instance.key}")
    return "".join(rng.choice(choice) for choice in instance.choices())


# --- objectives backed by the nodes that score them --------------------------


def cai_objective(weights: Mapping[str, float]) -> Objective:
    """CAI, scored exactly as the ``codon_adaptation`` node scores it."""
    from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
    from node_dag.nodes.tools.codon_adaptation.function import CodonAdaptation

    node = CodonAdaptation(CodonAdaptationConfig(codon_weights=dict(weights)))

    def score(sequence: str) -> float:
        return node.run(sequence=[Dna(sequence=sequence)])[0]["cai"].value

    return Objective(
        name="cai",
        score=score,
        optimum=lambda inst: exact_cai(inst, weights),
    )


def codon_pair_objective(
    pair_weights: Mapping[str, float], missing: float = 0.0
) -> Objective:
    """Codon pair score, scored exactly as the ``codon_pair_score`` node scores it."""
    from node_dag.nodes.tools.codon_pair_score.config import CodonPairScoreConfig
    from node_dag.nodes.tools.codon_pair_score.function import CodonPairScore

    node = CodonPairScore(
        CodonPairScoreConfig(pair_weights=dict(pair_weights), missing=missing)
    )

    def score(sequence: str) -> float:
        return node.run(sequence=[Dna(sequence=sequence)])[0]["codon_pair"].value

    return Objective(
        name="codon_pair",
        score=score,
        optimum=lambda inst: exact_codon_pair(inst, pair_weights, missing),
    )


# --- gates -------------------------------------------------------------------


def gate(instance: Instance, candidate: str) -> dict[str, float]:
    """The hard constraints of one candidate, through the ``constraint_check`` node.

    Reported separately from any score, so a candidate that changed the protein is a
    failure rather than a low number inside an average.
    """
    cfg = ConstraintCheckConfig(
        reference=instance.parent, immutable=tuple(sorted(instance.fixed()))
    )
    (row,) = ConstraintCheck(cfg).run(sequence=[Dna(sequence=candidate)])
    return {k: v.value for k, v in row.items()}


# --- running a strategy over instances ---------------------------------------


@dataclass(frozen=True)
class Result:
    """What one strategy achieved on one instance.

    Args:
        instance: The instance key, so the denominator stays the instance set.
        strategy: Which strategy produced the candidate.
        candidate: The recoded sequence, or None when the strategy produced nothing.
        score: The objective's score of the candidate, or None when there is none.
        optimum: The exact attainable optimum for this instance.
        parent_score: The original sequence's score, as the do-nothing reference.
        gates: The hard-constraint outcomes, all of them, pass or fail. Empty when
            the strategy produced no candidate, since there is nothing to check.
        evaluations: How many candidates the strategy scored to get here, which is the
            budget a comparison has to match.
    """

    instance: str
    strategy: str
    candidate: str | None
    score: float | None
    optimum: float
    parent_score: float
    gates: dict[str, float]
    evaluations: int

    @property
    def passed(self) -> bool:
        """Whether a candidate exists and every hard constraint held.

        ``immutable_unchanged`` is required because the protein check cannot see a
        swapped stop codon: all three stops translate to ``*``, so such a candidate
        keeps the protein while leaving the space the exact optimum is taken over, and
        would otherwise be scored against an optimum that could not have produced it.
        """
        if self.candidate is None:
            return False
        return (
            self.gates.get("protein_unchanged") == 1.0
            and self.gates.get("length_unchanged") == 1.0
            and self.gates.get("immutable_unchanged", 1.0) == 1.0
            and self.gates.get("targets_remaining", 0.0) == 0.0
        )

    @property
    def fraction_of_optimum(self) -> float | None:
        """``score / optimum``, or None when that would not mean anything.

        A failed gate, a missing candidate, a non-finite score and a non-positive
        optimum all give None rather than a number that could be averaged.

        Only interpretable for an objective that cannot go below zero. A score that
        can be negative, such as a log ratio of observed to expected frequency, gives
        a negative fraction, which is a real comparison but not a fraction of anything.
        Prefer ``gap_closed`` there.
        """
        if self.candidate is None or self.score is None or not self.passed:
            return None
        if not math.isfinite(self.score) or self.optimum <= 0:
            return None
        return self.score / self.optimum

    @property
    def gap_closed(self) -> float | None:
        """How much of the distance from the original to the optimum was covered.

        ``(score - parent) / (optimum - parent)``: 0.0 for leaving the sequence alone,
        1.0 for reaching the optimum, and negative for making the sequence worse than
        it started. Unlike ``fraction_of_optimum`` this reads the same whether or not
        the objective can go below zero, so it is the metric to compare strategies on.

        None when there is nothing to measure, or when the original already attains the
        optimum and the denominator vanishes.
        """
        if self.candidate is None or self.score is None or not self.passed:
            return None
        if not math.isfinite(self.score) or not math.isfinite(self.parent_score):
            return None
        span = self.optimum - self.parent_score
        if abs(span) < 1e-12:
            return None
        return (self.score - self.parent_score) / span


def run_strategy(
    instances: Sequence[Instance],
    objective: Objective,
    strategy: str,
    propose: Callable[[Instance], list[str]],
) -> list[Result]:
    """Run one strategy over every instance, keeping its best passing candidate.

    One row per instance comes back whether or not the strategy produced anything, so
    an instance cannot leave the denominator by failing.
    """
    rows: list[Result] = []
    for inst in instances:
        proposals = propose(inst)
        optimum = objective.score(objective.optimum(inst))
        parent = objective.score(inst.parent.sequence)
        scored: list[tuple[float, str]] = []
        for candidate in proposals:
            if gate(inst, candidate)["protein_unchanged"] != 1.0:
                continue  # A broken candidate cannot be the strategy's best.
            value = objective.score(candidate)
            if math.isfinite(value):
                scored.append((value, candidate))
        best = max(scored, default=None)
        rows.append(
            Result(
                instance=inst.key,
                strategy=strategy,
                candidate=best[1] if best else None,
                score=best[0] if best else None,
                optimum=optimum,
                parent_score=parent,
                gates=gate(inst, best[1]) if best else {},
                evaluations=len(proposals),
            )
        )
    return rows


def compare(
    instances: Sequence[Instance],
    objective: Objective,
    strategies: Mapping[str, Callable[[Instance], list[str]]],
) -> dict[str, list[Result]]:
    """Run every strategy over the same instances, in the same order."""
    return {
        name: run_strategy(instances, objective, name, propose)
        for name, propose in strategies.items()
    }


def per_instance(
    results: Sequence[Result], instances: Sequence[Instance]
) -> tuple[Outcome, ...]:
    """The results as per-instance outcomes, through the lineage mapping.

    Going through ``node_dag.lineage`` keeps the instance set as the denominator even
    when two instances recode to the same sequence, which would otherwise merge.
    """
    parents = {i.key: i.parent for i in instances}
    variants = tuple(
        Variant(instance=r.instance, index=0, entity=Dna(sequence=r.candidate))
        for r in results
        if r.candidate is not None
    )
    scores = {
        Dna(sequence=r.candidate).id: r.score
        for r in results
        if r.candidate is not None and r.score is not None
    }
    return outcomes(Lineage(parents=parents, variants=variants), scores)


# --- manifest ----------------------------------------------------------------


def _commit() -> str:
    """The current commit, or ``unknown`` outside a checkout."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def manifest(
    instances: Sequence[Instance],
    objective: Objective,
    weights: Mapping[str, float],
    seed: int,
    notes: str = "",
    split: "Split | None" = None,
) -> dict[str, object]:
    """What a run has to record to be auditable afterwards.

    This is what ``record_attempt`` stores per attempt. Pass ``split`` whenever one
    exists, so the hash of the division travels with the result: a number reported
    against a split nobody can identify is not evidence of independence.
    """
    return {
        "commit": _commit(),
        "split_hash": split.split_hash if split else None,
        "objective": objective.name,
        "seed": seed,
        "weights_sha256_16": _digest(dict(weights)),
        "instances": [
            {
                "key": i.key,
                "sequence_sha256_16": _digest(i.parent.sequence),
                "codons": len(codons(i.parent.sequence)),
                "immutable": sorted(i.fixed()),
            }
            for i in instances
        ],
        "instance_set_sha256_16": _digest([i.parent.sequence for i in instances]),
        "constraint_check_version": ConstraintCheckConfig.version,
        "notes": notes,
    }


def pair_count(sequence: str) -> int:
    """Adjacent in-frame codon pairs, as ``codon_pair_score`` counts them."""
    return max(len([c for c in codons(sequence) if len(c) == 3]) - 1, 0)


def mean_pair_weight(
    sequence: str, pair_weights: Mapping[str, float], missing: float = 0.0
) -> float:
    """The pair score of a sequence, computed directly rather than through the node.

    Used to check the node and the optimiser agree, which is the assumption the whole
    comparison rests on.
    """
    cs = [c for c in codons(sequence) if len(c) == 3]
    pairs = [a + b for a, b in pairwise(cs)]
    if not pairs:
        return 0.0
    return sum(pair_weights.get(p, missing) for p in pairs) / len(pairs)


# --- splitting ---------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """A fixed development and held-out division of an instance set.

    Development scores may inform a prompt, a configuration or a choice of strategy.
    Held-out scores may not, which is why they are reached through ``release_holdout``
    rather than by reading this field: an unlogged look leaves no trace, and a claim of
    independence then rests on nobody's memory.

    Args:
        dev: The instances that may be optimised against.
        holdout: The instances reserved for one frozen confirmation.
        salt: What the assignment was keyed by, recorded so it can be reproduced.
        holdout_fraction: The fraction asked for, which the realised split approximates.
    """

    dev: tuple[Instance, ...]
    holdout: tuple[Instance, ...]
    salt: str
    holdout_fraction: float

    @property
    def split_hash(self) -> str:
        """A hash of the assignment itself, for the manifest.

        Over keys and their side, not sequences, so it identifies the division rather
        than the data, and two runs can be compared on whether they split alike.
        """
        return _digest(
            {
                "dev": sorted(i.key for i in self.dev),
                "holdout": sorted(i.key for i in self.holdout),
                "salt": self.salt,
            }
        )

    def summary(self) -> dict[str, object]:
        """What the ledger records about the split, without the held-out sequences."""
        return {
            "split_hash": self.split_hash,
            "salt": self.salt,
            "holdout_fraction_asked": self.holdout_fraction,
            "n_dev": len(self.dev),
            "n_holdout": len(self.holdout),
            "dev_keys": sorted(i.key for i in self.dev),
            "holdout_keys": sorted(i.key for i in self.holdout),
        }


def split_instances(
    instances: Sequence[Instance], *, holdout_fraction: float = 0.3, salt: str = ""
) -> Split:
    """Divide instances deterministically, each one decided on its own key.

    Each instance's side comes from a hash of its key and the salt, so the division
    does not depend on the order instances arrive in, and adding one instance never
    moves another across. That matters when an instance set grows: a split that
    reshuffled would quietly turn held-out genes into development genes.

    Args:
        instances: The instance set to divide.
        holdout_fraction: Roughly what share to hold back. Because each key is decided
            independently the realised share only approximates it, which is the price
            of a stable assignment.
        salt: Changes the assignment. Record it; a new salt is a new split and
            invalidates any earlier claim of independence.
    """
    if not 0.0 <= holdout_fraction <= 1.0:
        raise ValueError(f"holdout_fraction must be in [0, 1], got {holdout_fraction}")
    dev, holdout = [], []
    cut = holdout_fraction * 2**32
    for inst in instances:
        digest = hashlib.sha256(f"{salt}:{inst.key}".encode()).digest()
        (holdout if int.from_bytes(digest[:4], "big") < cut else dev).append(inst)
    return Split(
        dev=tuple(dev),
        holdout=tuple(holdout),
        salt=salt,
        holdout_fraction=holdout_fraction,
    )


# --- the ledger --------------------------------------------------------------


def ledger_dir() -> Path:
    """``$NODE_DAG_RESULTS/ledger``. The root defaults to ``results``."""
    return Path(os.environ.get("NODE_DAG_RESULTS", "results")) / "ledger"


class Ledger:
    """An append-only record of attempts, one file per entry.

    One file per entry rather than one appended file: a partial append would corrupt
    earlier entries, and two processes appending at once would interleave. Writing a
    new file through ``write_atomic`` gives append-only semantics that survive both,
    and matches how the rest of ``results/`` is laid out.

    Nothing here rewrites or deletes an entry. An attempt that turned out badly stays,
    because a ledger that can be tidied afterwards cannot evidence what was tried.
    """

    def __init__(self, root: Path | None = None) -> None:
        """Keep entries under ``root``, defaulting to ``$NODE_DAG_RESULTS/ledger``."""
        self.root = root or ledger_dir()

    def append(self, kind: str, record: Mapping[str, object]) -> Path:
        """Write one entry and return its path.

        The name carries the time and a hash of the content, so entries sort
        chronologically and an identical attempt written twice does not collide
        silently.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        body = {
            "kind": kind,
            "recorded_at": datetime.now(UTC).isoformat(),
            **record,
        }
        payload = json.dumps(body, indent=2, sort_keys=True, default=str).encode()
        path = self.root / f"{stamp}-{kind}-{_digest(body)}.json"
        if path.exists():  # Same kind, same content, same microsecond.
            raise FileExistsError(f"Ledger entry already exists: {path}")
        write_atomic(path, payload)
        return path

    def entries(self) -> Iterator[dict[str, object]]:
        """Every entry, oldest first by filename."""
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*.json")):
            yield json.loads(path.read_text())

    def holdout_accesses(self) -> list[dict[str, object]]:
        """Every recorded look at held-out data.

        What a reader checks before believing a held-out number: one release, after
        the strategy was frozen, with a reason given.
        """
        return [e for e in self.entries() if e.get("kind") == "holdout_release"]


def dependency_versions(
    packages: Sequence[str] = ("pydantic", "biopython", "viennarna", "temporalio"),
) -> dict[str, str]:
    """The installed version of each package, or ``absent``.

    The cache keys configuration and inputs, not the environment, so a result is only
    reproducible alongside a record of what was installed.
    """
    out = {}
    for name in packages:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "absent"
    return out


def release_holdout(
    split: Split, *, reason: str, frozen: Mapping[str, object], ledger: Ledger
) -> tuple[Instance, ...]:
    """Record a held-out access, then return the held-out instances.

    The entry is written before the instances are handed over, so an access cannot
    happen without a trace even if what follows fails. ``frozen`` is what was fixed
    before looking: the strategy, its configuration, the development result it was
    chosen on. Recording it afterwards would prove nothing, since anything can be
    described as predeclared once the answer is known.

    Raises:
        ValueError: If no reason or nothing frozen is given. An unexplained release is
            the thing this is here to prevent.
    """
    if not reason.strip():
        raise ValueError("A held-out release needs a reason, recorded in the ledger.")
    if not frozen:
        raise ValueError(
            "A held-out release needs what was frozen beforehand: the strategy and "
            "the development result it was selected on."
        )
    ledger.append(
        "holdout_release",
        {
            "reason": reason,
            "frozen": dict(frozen),
            "split": split.summary(),
            "commit": _commit(),
            "dependencies": dependency_versions(),
        },
    )
    return split.holdout


def record_attempt(
    ledger: Ledger,
    *,
    manifest_: Mapping[str, object],
    split: Split | None,
    results: Mapping[str, Sequence[Result]],
    selection: str = "",
    errors: Sequence[str] = (),
    budget: Mapping[str, Mapping[str, int]] | None = None,
) -> Path:
    """Append one attempt: what ran, on what, with what budget and what came out.

    Per-instance metrics are kept rather than only their mean, so a later reader can
    see which instances failed instead of inferring it from an average. ``budget``
    maps a strategy to what it spent beyond ``evaluations``, e.g. a model-driven
    strategy's ``{"rounds": 3, "tokens": 102853}``. A fixed strategy has no entry.
    """
    return ledger.append(
        "attempt",
        {
            "manifest": dict(manifest_),
            "dependencies": dependency_versions(),
            "split": split.summary() if split else None,
            "selection": selection,
            "errors": list(errors),
            "strategies": {
                name: {
                    "evaluations": sum(r.evaluations for r in rows),
                    "passed": sum(r.passed for r in rows),
                    "n_instances": len(rows),
                    "mean_gap_closed": _mean([r.gap_closed for r in rows]),
                    "per_instance": [
                        {
                            "instance": r.instance,
                            "score": r.score,
                            "optimum": r.optimum,
                            "parent_score": r.parent_score,
                            "gap_closed": r.gap_closed,
                            "fraction_of_optimum": r.fraction_of_optimum,
                            "passed": r.passed,
                            "gates": r.gates,
                            "evaluations": r.evaluations,
                        }
                        for r in rows
                    ],
                    **(
                        {"budget": dict(budget[name])}
                        if budget and name in budget
                        else {}
                    ),
                }
                for name, rows in results.items()
            },
        },
    )


def _mean(values: Sequence[float | None]) -> float | None:
    """The mean of the values that exist, or None when none do."""
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


# --- weight tables from a reference set --------------------------------------


def cai_weights_from(sequences: Sequence[str]) -> dict[str, float]:
    """Relative adaptiveness of every codon over a reference set.

    Each codon's count divided by the highest count among synonyms of its amino acid,
    which is the weight a codon adaptation index is built from. Derive it from genes
    that are not in the instance set: a gene scored against statistics computed from
    itself is being compared with a ruler it helped make.

    An amino acid absent from the reference gets zero for all its codons, which the
    ``codon_adaptation`` node then skips rather than treating as a zero term.
    """
    counts: dict[str, int] = dict.fromkeys(CODON_TABLE, 0)
    for sequence in sequences:
        for codon in codons(sequence):
            if codon in counts:
                counts[codon] += 1
    weights: dict[str, float] = {}
    for syns in SYNONYMS.values():
        top = max((counts[c] for c in syns), default=0)
        for c in syns:
            weights[c] = (counts[c] / top) if top else 0.0
    return weights


def pair_weights_from(
    sequences: Sequence[str], floor: float = math.log(0.1)
) -> dict[str, float]:
    """Log observed over expected frequency of each adjacent codon pair.

    Expected assumes the two codons are independent, so this measures how far adjacent
    use departs from that. It is deliberately not the Coleman codon-pair-bias
    statistic, which conditions on the amino-acid pair as well; this is the plainer
    measure, named for what it is.

    A pair never seen is not in the table, and the scorer's ``missing`` handles it.
    A pair seen far less often than expected is held at ``floor`` so one rare pair
    cannot dominate a mean through a large negative log.
    """
    codon_counts: dict[str, int] = {}
    pair_counts: dict[str, int] = {}
    for sequence in sequences:
        cs = [c for c in codons(sequence) if len(c) == 3 and c in CODON_TABLE]
        for c in cs:
            codon_counts[c] = codon_counts.get(c, 0) + 1
        for a, b in pairwise(cs):
            pair_counts[a + b] = pair_counts.get(a + b, 0) + 1
    total_codons = sum(codon_counts.values())
    total_pairs = sum(pair_counts.values())
    if not total_codons or not total_pairs:
        return {}
    weights: dict[str, float] = {}
    for pair, observed in pair_counts.items():
        a, b = pair[:3], pair[3:]
        expected = (
            total_pairs
            * (codon_counts[a] / total_codons)
            * (codon_counts[b] / total_codons)
        )
        weights[pair] = max(math.log(observed / expected), floor) if expected else floor
    return weights


class GoalSpec(TypedDict):
    """A goal as the loop takes it: a ``Hypothesis`` without an id.

    Typed rather than a loose dict so a caller reading ``criteria`` or ``goal`` gets a
    string and a list, not ``object``.
    """

    goal: str
    inputs: dict[str, list[dict[str, str]]]
    criteria: list[dict[str, str]]


# --- goals for the hypothesis loop -------------------------------------------


def goal_for(instance: Instance, weights: Mapping[str, float]) -> GoalSpec:
    """One instance as a goal the loop can be asked, in the words the gate scores.

    The sequence, the weight table and the codons that must not change are the
    benchmark's own, so the loop is asked the question the gate answers. Hand-written
    goals left the fixed codons out, which let a synonymous stop swap through: it keeps
    the protein, so it passes a protein check, while leaving the space the exact optimum
    is taken over.

    The optimum, any gap and the split are never written. A goal carrying its own answer
    would be worthless, and a goal naming its side of the split would leak it.

    Returns a ``Hypothesis`` without an id, which the loop fills in.
    """
    table = json.dumps(dict(sorted(weights.items())), separators=(",", ":"))
    cs = codons(instance.parent.sequence)
    fixed = sorted(instance.fixed())
    keep = ", ".join(f"{cs[i]} at index {i}" for i in fixed)
    claims = {
        "protein_preserved": (
            "Every kept output DNA sequence translates to exactly the same protein as "
            "the input sequence: only synonymous codon changes were made."
        ),
        "higher_cai": (
            "Every kept output sequence has a codon adaptation index, scored with the "
            "given weight table, strictly higher than the input sequence's."
        ),
        "only_improved_kept": (
            "Any sequence whose codon adaptation index is not higher than the input "
            "sequence's is excluded from the output."
        ),
        "length_preserved": (
            "Every kept output sequence has the same length as the input sequence."
        ),
        "fixed_codons_kept": (
            f"Every kept output sequence keeps the input's codon at each fixed position "
            f"({keep}; codon indices count from 0). A synonymous codon there is a change."
        ),
    }
    return {
        "goal": (
            "Raise the codon adaptation index of the DNA sequence without changing its "
            f"protein. Score it with this codon weight table: {table} . "
            f"Leave these codons as they are: {keep}. "
            "Check them with constraint_check, using the input as reference and "
            f"immutable={fixed}, then filter immutable_unchanged at 1. "
            "Keep only the sequences scoring higher than the input."
        ),
        "inputs": {"seqs": [{"kind": "dna", "sequence": instance.parent.sequence}]},
        "criteria": [
            {"id": i, "claim": c, "source": "human"} for i, c in claims.items()
        ],
    }
