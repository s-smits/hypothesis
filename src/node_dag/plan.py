"""A goal, what counts as meeting it, and the plans tried against it.

The types here are shared by the agents and the workflow, so this module must not import
``pydantic_ai``: ``temporal/hypothesis/workflow.py`` needs ``Hypothesis`` and ``Plan``
inside the Temporal sandbox, and pulling the agent graph in there would break it.

The rule that holds the whole loop together lives here as :func:`accepted`: a run is
achieved only when every :class:`Criterion` is covered by an :class:`Assertion` that the
DAG actually satisfied. A model may veto that, never grant it.
"""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from node_dag.dag import Dag, DagOutput
from node_dag.nodes.base import Category
from node_dag.types import TYPES, Value
from node_dag.wiring import PortContract


class Criterion(BaseModel):
    """One thing that must be true for the goal to be met.

    Fixed when the Hypothesis is created, before any plan exists, and never changed after.
    The builder chooses how to satisfy the criteria; it never chooses what they are.

    Args:
        id: A short slug naming it, e.g. ``no_tcg``.
        claim: What must be true, in plain English.
        source: ``human`` when typed on the new-hypothesis page, ``derived`` when the
            criteria agent proposed it and the human confirmed it.
    """

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    claim: str = Field(min_length=5)
    source: Literal["human", "derived"] = "human"


class ConfigField(BaseModel):
    """One config field a requested node needs.

    Args:
        name: The field name, snake_case.
        type: Its JSON type. Keep it to a scalar or a list of scalars; anything richer
            belongs on an input port, not in the config.
        description: What it controls.
        required: False only when ``default`` is set.
        default: The default, as JSON.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["str", "int", "float", "bool", "list[str]"]
    description: str = Field(min_length=5)
    required: bool = True
    default: Any | None = None


class ToolRequest(BaseModel):
    """The full contract of a node that does not exist yet.

    Enough for a human to write ``config.py`` and ``function.py`` without asking a
    question, and enough to typecheck a plan that uses it before anyone writes it.

    Args:
        name: The node name the plan uses, snake_case, not an existing node.
        node: ``tool`` returns ``output``; ``decision`` returns a bool and forwards one
            input on ``.yes`` / ``.no``.
        purpose: What one step of it does, in one sentence.
        category: Which Category it belongs to.
        inputs: Each input port to the kind it takes. Kinds must be in TYPES.
        output: The kind ``run`` returns. A tool must set it; a decision must not.
        forwards: The input port a decision forwards. A decision must set it.
        config_fields: The fields its config declares.
        why_needed: What the goal needs that no existing node gives.
        why_not_composable: Why the existing nodes cannot be wired to do this. Must name
            the nodes that were considered.
        example: One worked example: concrete inputs, the config, and the output.
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    node: Literal["tool", "decision"]
    purpose: str = Field(min_length=10)
    category: Category
    inputs: dict[str, str] = Field(min_length=1)
    output: str | None = None
    forwards: str | None = None
    config_fields: list[ConfigField] = []
    why_needed: str = Field(min_length=20)
    why_not_composable: str = Field(min_length=20)
    example: str = Field(min_length=10)

    @model_validator(mode="after")
    def _check(self) -> Self:
        """A tool has an output; a decision forwards one of its own ports."""
        kinds = sorted(TYPES)
        if bad := sorted(set(self.inputs.values()) - set(TYPES)):
            raise ValueError(f"Unknown input kinds {bad}; known: {kinds}")
        if self.node == "tool":
            if self.output is None:
                raise ValueError(f"{self.name}: a tool must declare an output kind")
            if self.output not in TYPES:
                raise ValueError(f"Unknown output kind {self.output!r}; known: {kinds}")
            if self.forwards is not None:
                raise ValueError(f"{self.name}: only a decision forwards a port")
        else:
            if self.output is not None:
                raise ValueError(f"{self.name}: a decision returns a bool, not a kind")
            if self.forwards not in self.inputs:
                raise ValueError(
                    f"{self.name}: forwards must be one of its ports "
                    f"{sorted(self.inputs)}, got {self.forwards!r}"
                )
        return self

    def contract(self) -> PortContract:
        """This request as a port contract, the same shape an existing node gives.

        What lets a plan naming nodes nobody has written be typechecked exactly like a
        Dag: the request declares its ports and kinds, so ``check_wiring`` cannot tell
        the difference.
        """
        return PortContract(
            inputs=dict(self.inputs), output=self.output, forwards=self.forwards
        )


class PlannedStep(BaseModel):
    """One step of a plan. Its node may or may not exist yet.

    ``node`` is lifted out of the config so a step on a node nobody has written needs no
    config validation to be read.

    Args:
        node: The node name: an existing node, or the name of a ToolRequest.
        config: The node's config fields, without ``name``.
        inputs: Each input port to a source, as in ``Step.inputs``.
        why: Why this step, here, with this config.
    """

    node: str
    config: dict[str, Any] = {}
    inputs: dict[str, str]
    why: str = Field(min_length=10)

    def draft(self) -> dict[str, Any]:
        """This step as a Dag ``Step`` payload."""
        return {"config": {"name": self.node, **self.config}, "inputs": self.inputs}


class Assertion(BaseModel):
    """A machine-checkable claim about what the DAG will produce.

    The prediction, made executable. ``step`` must be a decision step of the same plan,
    so the claim is settled by the branch the DAG actually took and no model judges it.

    Args:
        criterion: The id of the Criterion this tests.
        step: The decision step whose branch settles it.
        branch: The branch that must fire for the claim to hold.
        claim: What this proves about the goal, for a human reading the result.
    """

    criterion: str
    step: str
    branch: Literal["yes", "no"]
    claim: str = Field(min_length=5)

    def source(self) -> str:
        """The ``DagOutput.values`` key that must exist for this assertion to hold."""
        return f"{self.step}.{self.branch}"


class Plan(BaseModel):
    """The builder's best plan for a goal, whether or not its nodes exist.

    Validation here is **shape only**. It must never consult the node registry: a Plan is
    deserialized inside the Temporal workflow on every replay, and the registry changes
    under a running workflow whenever someone adds a node. Registry-aware checks belong in
    ``submit_plan`` and the ``resolve_plan`` activity, both of which run activity-side.

    Args:
        hypothesis: How this plan meets the goal.
        expected: The prediction in prose, for a human.
        assertions: The prediction, machine-checked. One per criterion, at least.
        inputs: Each DAG input name to its kind. Must be the goal's inputs.
        steps: The steps, keyed by name. A key must not contain ``.``.
        requests: A contract for each node the plan names that may not exist, keyed by
            node name.
        reasons: Why each existing node was chosen, keyed by node name.
        addresses_critique: What this plan changes in response to the last critique.
            Empty on the first round.
    """

    hypothesis: str = Field(min_length=10)
    expected: str = Field(min_length=10)
    assertions: list[Assertion] = []
    inputs: dict[str, str]
    steps: dict[str, PlannedStep] = Field(min_length=1)
    requests: dict[str, ToolRequest] = {}
    reasons: dict[str, str] = {}
    addresses_critique: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        """Shape only: step keys, known input kinds, requests keyed by their own name."""
        if bad := [k for k in self.steps if "." in k or k in self.inputs]:
            raise ValueError(
                f"Step keys must not contain '.' or repeat an input: {bad}"
            )
        if bad := {k: v for k, v in self.inputs.items() if v not in TYPES}:
            raise ValueError(f"Unknown input kinds {bad}; known: {sorted(TYPES)}")
        if wrong := [k for k, r in self.requests.items() if k != r.name]:
            raise ValueError(f"Requests must be keyed by their own name: {wrong}")
        return self

    def node_names(self) -> set[str]:
        """Every node name this plan uses, existing or requested."""
        return {s.node for s in self.steps.values()}

    def fingerprint(self) -> str:
        """A hash of the wiring alone, so re-wording cannot dodge the repeat check.

        Deliberately excludes ``hypothesis``, ``expected``, ``why`` and ``reasons``: a
        later round must not be able to resubmit an earlier graph with new adjectives.
        """
        key = {
            "inputs": self.inputs,
            "steps": {
                k: {"node": s.node, "config": s.config, "inputs": s.inputs}
                for k, s in sorted(self.steps.items())
            },
        }
        return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


class Critique(BaseModel):
    """Why an attempt did not meet the goal, and what the next plan must change.

    Args:
        diagnosis: What went wrong, in terms of this DAG's steps and values.
        root_cause: The one thing most responsible. A closed set, so that no-progress
            detection and the blocked-tool tally can read it.
        evidence: The step keys and values that show it.
        keep: Step keys that were right. The next plan should not churn them.
        fix: What the next plan must do differently, concretely.
        reason: Why this diagnosis and not another one that was considered.
    """

    diagnosis: str = Field(min_length=20)
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
    fix: str = Field(min_length=20)
    reason: str = Field(min_length=20)


class VerifyOpinion(BaseModel):
    """What the verifier agent is allowed to return.

    Deliberately smaller than :class:`Verdict`: there is no ``achieved`` field here, so
    the model has no way to declare success even if it wants to. The workflow computes
    that from :func:`accepted` and builds the full Verdict around this opinion.

    Args:
        agrees: Whether the outcome meets the goal, in the model's judgement. Taken as a
            veto when false; it grants nothing when true.
        covers_goal: Whether the assertions genuinely test the criteria. Say false when
            the checks are superficial, or when a criterion was judged by eye rather
            than by a node.
        reason: The expected result, the actual result, and how they compare.
        score: How close the outcome came, 0 nothing right, 1 fully achieved.
    """

    agrees: bool
    covers_goal: bool
    reason: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class Verdict(BaseModel):
    """How an attempt was judged.

    ``achieved`` is computed by :func:`accepted` and set by the workflow. The verify
    activity returns only ``agrees``, ``covers_goal``, ``score`` and ``reason``: a model
    may veto a result, never certify one.

    Args:
        agrees: The model's opinion that the outcome meets the goal. A veto only.
        covers_goal: Whether the assertions genuinely test the criteria, in the model's
            judgement. False blocks acceptance.
        reason: The expected result, the actual result, and how they compare.
        score: How close the outcome came, 0 nothing right, 1 fully achieved.
        prediction_held: Whether every assertion's branch fired. Computed, not judged.
        achieved: Whether the run met its goal. Computed, not judged.
    """

    agrees: bool = False
    covers_goal: bool = True
    reason: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    prediction_held: bool = False
    achieved: bool = False


class Attempt(BaseModel):
    """One round: the plan, what it needed, what it ran, and how it was judged.

    Args:
        round: 1-based round number.
        plan: The plan the builder submitted.
        fingerprint: That plan's wiring hash.
        requests: The contracts that were missing when this round blocked.
        resumes: One note per ``tool_added`` signal: what re-resolving found.
        workflow_id: The child DagWorkflow that ran this round's DAG.
        dag: The DAG built from the plan, once nothing was missing.
        outcome: Every value that DAG produced.
        verdict: How it was judged.
        critique: What to change next, when it was not achieved.
        error: Why the round failed, if it did.
        trajectories: Each agent stage to the file holding its message history.
        usage: Tokens used this round, keyed by stage.
        started: When the round began.
        finished: When the round ended.
    """

    round: int = Field(ge=1)
    plan: Plan | None = None
    fingerprint: str | None = None
    requests: list[ToolRequest] = []
    resumes: list[str] = []
    workflow_id: str | None = None
    dag: Dag | None = None
    outcome: DagOutput | None = None
    verdict: Verdict | None = None
    critique: Critique | None = None
    error: str | None = None
    trajectories: dict[str, str] = {}
    usage: dict[str, int] = {}
    started: datetime
    finished: datetime | None = None


class AttemptSummary(BaseModel):
    """One prior attempt as the builder needs to see it.

    Complete on reasoning, compact on data: the builder must know *that* a step produced a
    2970-base sequence, not what its bases were. Keeping the full values here would push
    the activity payload towards Temporal's 2 MB limit after a few rounds.

    Args:
        round: Which round this was.
        fingerprint: That plan's wiring hash, so the builder cannot resubmit it.
        hypothesis: What that plan claimed.
        expected: What it predicted.
        assertions: The claims it committed to.
        held: Each assertion's source to whether its branch fired.
        steps: Each step key to its node, config and wiring.
        requested: The tool names that round asked for.
        verdict: How it was judged.
        critique: What the critic said to change.
        error: Why it failed, if it did.
        produced: Each source to a short description of its value, never the value.
    """

    round: int
    fingerprint: str | None = None
    hypothesis: str
    expected: str
    assertions: list[Assertion] = []
    held: dict[str, bool] = {}
    steps: dict[str, dict[str, Any]] = {}
    requested: list[str] = []
    verdict: Verdict | None = None
    critique: Critique | None = None
    error: str | None = None
    produced: dict[str, str] = {}


HypothesisState = Literal[
    "building",
    "running",
    "verifying",
    "critiquing",
    "blocked",
    "achieved",
    "not achieved",
    "unverified",
    "abandoned",
    "failed",
]


class Hypothesis(BaseModel):
    """A goal, what counts as meeting it, and every plan tried against it.

    Set ``goal``, ``inputs`` and ``criteria``. ``HypothesisWorkflow`` fills in the rest.
    The flat fields ``hypothesis``, ``dag``, ``workflow_id``, ``outcome`` and ``verdict``
    mirror the current attempt, so pages and callers written against the single-pass
    version keep working; ``attempts`` holds the full history.

    Args:
        id: Names the saved file, and the workflow that runs it.
        goal: What the DAG must do, in plain English.
        criteria: What must be true for the goal to be met. Frozen before any plan.
        inputs: The values to run on, keyed by DAG input name.
        hypothesis: The current attempt's plan, in prose.
        dag: The current attempt's DAG.
        workflow_id: The child DagWorkflow run that ran it.
        outcome: What that DAG produced.
        verdict: How it was judged.
        state: Where the run has got to. None for files written before states existed.
        round: The current round number.
        attempts: Every round so far.
        pending: The tool contracts this run is blocked on.
        critique: The latest critique.
        best_score: The best score any round reached.
        usage: Tokens used across the whole run, keyed by stage.
        stopped_because: Why the loop ended.
    """

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid.uuid4()}")
    goal: str
    criteria: list[Criterion] = []
    inputs: dict[str, Value]
    hypothesis: str | None = None
    dag: Dag | None = None
    workflow_id: str | None = None
    outcome: DagOutput | None = None
    verdict: Verdict | None = None
    state: HypothesisState | None = None
    round: int = 0
    attempts: list[Attempt] = []
    pending: list[ToolRequest] = []
    critique: Critique | None = None
    best_score: float = 0.0
    usage: dict[str, int] = {}
    stopped_because: str | None = None

    def input_kinds(self) -> dict[str, str]:
        """The DAG inputs the builder must declare: name to kind."""
        return {k: v.kind for k, v in self.inputs.items()}

    def criterion_ids(self) -> set[str]:
        """The ids of every criterion this hypothesis must satisfy."""
        return {c.id for c in self.criteria}


def holds(assertions: list[Assertion], outcome: DagOutput | None) -> dict[str, bool]:
    """Whether each assertion's decision step took the branch the plan predicted.

    Pure: no model is consulted. An assertion holds when the branch it named produced a
    value, which is how ``DagWorkflow`` records the branch a decision took.

    Args:
        assertions: The plan's claims.
        outcome: What the DAG produced, or None when it did not run.
    """
    values = outcome.values if outcome else {}
    return {a.source(): a.source() in values for a in assertions}


def accepted(
    criteria: list[Criterion], plan: Plan | None, verdict: Verdict | None,
    outcome: DagOutput | None,
) -> tuple[bool, str]:
    """Whether a round met the goal, and why not when it did not.

    An agent may veto this, never grant it. Acceptance needs every criterion covered by an
    assertion, every assertion's branch fired, and no veto from the verifier. A plan that
    asserts nothing can never be accepted: that run is ``unverified``, which is a request
    for a checking node rather than a failure of the science.

    Args:
        criteria: What must be true. Frozen before the plan was written.
        plan: The plan that ran.
        verdict: The verifier's opinion.
        outcome: What the DAG produced.
    """
    if plan is None or verdict is None:
        return False, "the round did not produce a verdict"
    wanted = {c.id for c in criteria}
    if not wanted:
        return False, "the hypothesis has no criteria, so nothing could be checked"
    if not plan.assertions:
        return False, "the plan asserted nothing, so nothing could be checked"
    if missing := sorted(wanted - {a.criterion for a in plan.assertions}):
        return False, f"no assertion covers {missing}"
    if failed := sorted(s for s, ok in holds(plan.assertions, outcome).items() if not ok):
        return False, f"these assertions did not hold: {failed}"
    if not verdict.covers_goal:
        return False, "the verifier judged the assertions not to cover the goal"
    if not verdict.agrees:
        return False, "the verifier did not agree the goal was met"
    return True, "every criterion was covered by an assertion that held"
