"""A goal's criteria, the plans tried against it, and the rule that accepts a round.

Must not import ``pydantic_ai``: the Temporal workflow imports this in its sandbox.
Validation here is shape only; checks against the node registry live activity-side.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, Field, create_model, model_validator

from node_dag.dag import Dag, DagOutput, Step
from node_dag.nodes.base import (
    BaseFilterConfig,
    BaseNodeConfig,
    BaseScoreConfig,
    BaseToolConfig,
    Category,
)
from node_dag.types import TYPES, Entity, Score

SLUG = r"^[a-z][a-z0-9_]*$"
JSON_TYPES = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list[str]": list[str],
}


class Criterion(BaseModel):
    """One thing that must be true for the goal to be met. Frozen before any plan.

    ``source`` says who wrote it: a person (typed or edited by them), or the criteria
    agent, which the loop sets itself.
    """

    id: str = Field(pattern=SLUG)
    claim: str
    source: Literal["human", "derived"] = "human"


def repeated(criteria: list[Criterion]) -> list[str]:
    """The ids that more than one criterion uses, which one assertion would cover at once."""
    return sorted(i for i, n in Counter(c.id for c in criteria).items() if n > 1)


class ConfigField(BaseModel):
    """One config field a requested node needs."""

    name: str = Field(pattern=SLUG)
    type: Literal["str", "int", "float", "bool", "list[str]"]
    description: str
    required: bool = True
    default: str | int | float | bool | list[str] | None = None


class ToolRequest(BaseModel):
    """The contract of a node that does not exist yet."""

    name: str = Field(pattern=SLUG)
    node: Literal["tool", "score", "filter"]
    purpose: str
    port: str = "sequence"
    kind: str
    output: str | list[str] | None = None
    config_fields: list[ConfigField] = []
    why_needed: str
    why_not_composable: str
    example: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        kinds = sorted(TYPES)
        if self.kind not in TYPES:
            raise ValueError(f"Unknown kind {self.kind!r}; known: {kinds}")
        if self.node == "tool" and self.output not in TYPES:
            raise ValueError(f"A tool needs an output kind from {kinds}")
        if self.node == "score" and not (isinstance(self.output, list) and self.output):
            raise ValueError("A scorer needs a list of score names as its output")
        if self.node == "filter" and self.output is not None:
            raise ValueError("A filter has no output")
        return self

    def stand_in(self) -> Callable[..., BaseNodeConfig]:
        """A config class with this contract, to typecheck a plan before the node exists."""
        base, category = {
            "tool": (BaseToolConfig, Category.GENERATION),
            "score": (BaseScoreConfig, Category.SCORING),
            "filter": (BaseFilterConfig, Category.FILTER),
        }[self.node]
        fields: dict[str, Any] = {
            f.name: (JSON_TYPES[f.type], ... if f.required else f.default)
            for f in self.config_fields
        }
        # The port is set as the class is made: a score or filter must have its one then.
        port = (ClassVar[dict[str, type[Entity]]], {self.port: TYPES[self.kind]})
        cls = create_model(
            f"Requested_{self.name}",
            __base__=base,
            name=(Literal[self.name], self.name),  # ty: ignore[invalid-type-form]
            inputs=port,
            **fields,
        )
        cls.categories = (category,)
        # The base is only known at run time, so the type checker cannot see its ``output``.
        if isinstance(self.output, str):
            cls.output = TYPES[self.output]  # ty: ignore[invalid-assignment]
        elif isinstance(self.output, list):
            cls.output = {n: Score for n in self.output}  # ty: ignore[invalid-assignment]
        return cls


class PlannedStep(BaseModel):
    """One step of a plan."""

    node: str
    config: dict[str, Any] = {}
    inputs: dict[str, str]
    why: str


class Assertion(BaseModel):
    """A claim the DAG settles by which branch of a filter step it takes.

    ``produced`` is for a goal with nothing to filter, such as scoring or converting: on a
    tool or score step it holds when the step gave output, and says nothing about the
    output being right.
    """

    criterion: str
    step: str
    branch: Literal["yes", "no", "produced"]
    claim: str


class DraftObservation(BaseModel):
    """A finding from an Amass record that bears on the hypothesis.

    Args:
        amass_id: The record's amassId, from search_literature or get_record.
        summary: What the record found that bears on this hypothesis, and how it
            shaped the DAG, in two or three sentences.
    """

    amass_id: str
    summary: str


class Observation(DraftObservation):
    """A finding from the literature, with where it came from.

    Args:
        core: The Amass core the record is in, e.g. ``biomedcore``.
        title: The record's title.
        url: Where to read the record, if it has a link.
        source: The journal, or whatever else published it.
        date: When it was published.
    """

    core: str
    title: str
    url: str | None = None
    source: str | None = None
    date: str | None = None

    @classmethod
    def from_record(
        cls, draft: DraftObservation, core: str, record: dict[str, Any]
    ) -> "Observation":
        """The draft, with the title, link and source filled in from its record."""
        return cls(
            **draft.model_dump(),
            core=core,
            title=record.get("title") or record.get("name") or draft.amass_id,
            url=record.get("url"),
            source=record.get("journal"),
            date=record.get("publicationDate"),
        )


class Plan(BaseModel):
    """The builder's plan for a goal, whether or not its nodes exist."""

    hypothesis: str
    expected: str
    assertions: list[Assertion] = []
    inputs: dict[str, str] = Field(
        description="The goal's inputs, as shown: each input's name and the kind of its "
        'entities, e.g. {"seqs": "dna"}. Not a source name.'
    )
    steps: dict[str, PlannedStep] = Field(min_length=1)
    requests: dict[str, ToolRequest] = {}
    observations: list[DraftObservation] = Field(
        default=[],
        description="The findings from search_literature or get_record that bear on the "
        "hypothesis, one per record. Leave out if you did not search.",
    )
    addresses_critique: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if wrong := [k for k, r in self.requests.items() if k != r.name]:
            raise ValueError(f"Requests must be keyed by their own name: {wrong}")
        return self

    def fingerprint(self) -> str:
        """A hash of the wiring alone, so re-wording cannot dodge the repeat check."""
        wiring = {
            k: [s.node, s.config, s.inputs] for k, s in sorted(self.steps.items())
        }
        return hashlib.sha256(
            json.dumps([self.inputs, wiring], sort_keys=True).encode()
        ).hexdigest()

    def typecheck(self, configs: dict[str, BaseNodeConfig]) -> None:
        """Run ``Dag._check`` over this plan's steps, given a config for each."""
        steps = {
            k: Step.model_construct(config=configs[k], inputs=s.inputs)
            for k, s in self.steps.items()
        }
        # pydantic wraps the validator, which the type checker cannot see through.
        Dag.model_construct(inputs=self.inputs, steps=steps)._check()  # ty: ignore[call-non-callable]


class Critique(BaseModel):
    """Why an attempt missed, and what the next plan must change."""

    diagnosis: str
    root_cause: Literal[
        "wrong_node",
        "wrong_wiring",
        "wrong_config",
        "missing_tool",
        "goal_misread",
        "node_raised",
    ]
    evidence: list[str] = Field(min_length=1)
    keep: list[str] = []
    fix: str


class VerifyOpinion(BaseModel):
    """What the verifier returns. It has no ``achieved``: it can only veto."""

    agrees: bool
    covers_goal: bool
    reason: str


class Verdict(BaseModel):
    """How a round was judged. ``achieved`` is computed by :func:`accepted`."""

    achieved: bool
    reason: str
    agrees: bool = False
    covers_goal: bool = True


class Attempt(BaseModel):
    """One round: the plan, what it needed, what it ran, and how it was judged.

    ``workflow_id`` is the DagWorkflow run that ran ``dag``. ``held`` and ``produced``
    outlive ``outcome``, which older rounds drop to keep the Hypothesis small; the full
    outcome stays in the child run's saved file.
    """

    round: int
    plan: Plan | None = None
    requests: list[ToolRequest] = []
    dag: Dag | None = None
    workflow_id: str | None = None
    outcome: DagOutput | None = None
    held: dict[str, bool] = {}
    produced: dict[str, list[str]] = {}
    verdict: Verdict | None = None
    critique: Critique | None = None
    error: str | None = None
    started: datetime | None = None

    def summary(self) -> dict[str, Any]:
        """This round for the builder's prompt: reasoning in full, data as previews."""
        p = self.plan
        return {
            "round": self.round,
            "hypothesis": p and p.hypothesis,
            "steps": p
            and {k: [s.node, s.config, s.inputs] for k, s in p.steps.items()},
            "held": self.held,
            "produced": self.produced,
            "verdict": self.verdict and self.verdict.reason,
            "critique": self.critique and self.critique.model_dump(),
            "error": self.error,
        }


def preview(outcome: DagOutput) -> dict[str, list[str]]:
    """Each source's first three entities as head, tail and length, never the sequence."""
    shown = lambda i: f"{i.display[:12]}..{i.display[-6:]} ({len(i.display)})"
    return {k: [shown(i) for i in t.items[:3]] for k, t in outcome.values.items()}


HypothesisState = Literal[
    "building", "running", "verifying", "critiquing", "blocked",
    "achieved", "not achieved", "abandoned", "failed",
]  # fmt: skip


def holds(assertions: list[Assertion], outcome: DagOutput | None) -> dict[str, bool]:
    """Whether each assertion's branch took every entity and the other took none.

    A ``produced`` assertion holds when its step gave at least one entity.
    """
    values = outcome.values if outcome else {}

    def n(source: str) -> int:
        return len(values[source].items) if source in values else 0

    def holds_one(a: Assertion) -> bool:
        if a.branch == "produced":
            return n(a.step) > 0
        other = "no" if a.branch == "yes" else "yes"
        return n(f"{a.step}.{a.branch}") > 0 and n(f"{a.step}.{other}") == 0

    return {f"{a.step}.{a.branch}": holds_one(a) for a in assertions}


def accepted(
    criteria: list[Criterion],
    plan: Plan | None,
    opinion: VerifyOpinion | None,
    outcome: DagOutput | None,
) -> tuple[bool, str]:
    """Whether a round met the goal, and why not. A model may veto this, never grant it."""
    if plan is None or opinion is None:
        return False, "the round produced no plan or verifier opinion"
    if not criteria:
        return False, "the hypothesis has no criteria, so nothing could be checked"
    if dup := repeated(criteria):
        return False, f"criteria share the ids {dup}, so one assertion would cover both"
    if missing := sorted(
        {c.id for c in criteria} - {a.criterion for a in plan.assertions}
    ):
        return False, f"no assertion covers {missing}"
    if failed := sorted(
        s for s, ok in holds(plan.assertions, outcome).items() if not ok
    ):
        return False, f"these assertions did not hold: {failed}"
    if not opinion.covers_goal:
        return False, "the verifier judged the assertions not to cover the goal"
    if not opinion.agrees:
        return False, "the verifier did not agree the goal was met"
    return True, "every criterion was covered by an assertion that held"
