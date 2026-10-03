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
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from node_dag.dna import CODON_TABLE, SYNONYMS, codons
from node_dag.lineage import Lineage, Outcome, Variant, outcomes
from node_dag.nodes.tools.constraint_check.config import ConstraintCheckConfig
from node_dag.nodes.tools.constraint_check.function import ConstraintCheck
from node_dag.types import Dna

__all__ = [
    "Instance",
    "Objective",
    "Result",
    "cai_objective",
    "codon_pair_objective",
    "compare",
    "exact_cai",
    "exact_codon_pair",
    "greedy_chain",
    "manifest",
    "random_synonymous",
    "run_strategy",
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

    def choices(self) -> list[tuple[str, ...]]:
        """The codons allowed at each position, in order.

        An immutable position, a codon with no synonym and a trailing partial codon
        all give a single choice, so a caller does not special-case them.
        """
        cs = codons(self.parent.sequence)
        fixed = self.immutable or default_immutable(self.parent.sequence)
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
    cfg = ConstraintCheckConfig(reference=instance.parent)
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
        """Whether a candidate exists and every hard constraint held."""
        if self.candidate is None:
            return False
        return (
            self.gates.get("protein_unchanged") == 1.0
            and self.gates.get("length_unchanged") == 1.0
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
) -> dict[str, object]:
    """What a run has to record to be auditable afterwards.

    This is the subset an append-only ledger would keep per attempt. It is not the
    ledger itself, and it holds no split hash, since nothing is held back yet.
    """
    return {
        "commit": _commit(),
        "objective": objective.name,
        "seed": seed,
        "weights_sha256_16": _digest(dict(weights)),
        "instances": [
            {
                "key": i.key,
                "sequence_sha256_16": _digest(i.parent.sequence),
                "codons": len(codons(i.parent.sequence)),
                "immutable": sorted(
                    i.immutable or default_immutable(i.parent.sequence)
                ),
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
