"""What crosses the workflow-activity boundary for the hypothesis loop.

Kept apart from ``activities`` on purpose: the activity implementations import
``pydantic_ai`` to build agents, while ``workflow`` needs only these models. Splitting
them keeps the agent import graph out of the Temporal sandbox.

Everything here must be serializable by ``pydantic_data_converter`` and must stay well
inside Temporal's 2 MB payload limit, which is why history is summarised rather than
passed whole.
"""

from typing import Any

from pydantic import BaseModel, Field

from node_dag.dag import Dag, DagOutput
from node_dag.plan import (
    Assertion,
    AttemptSummary,
    Criterion,
    Critique,
    Hypothesis,
    Plan,
    ToolRequest,
    Verdict,
)
from node_dag.types import Value
from node_dag.wiring import PortContract


class Brief(BaseModel):
    """What the literature says bears on a goal. Phase 2; empty until then.

    Args:
        findings: What was found, each with where it came from.
        suggested_criteria: Criteria proposed from those findings. Not binding until a
            human confirms them.
        optimise: What a good plan should maximise or minimise.
        avoid: Known failure modes a plan should steer around.
    """

    findings: list["Finding"] = []
    suggested_criteria: list[Criterion] = []
    optimise: list[str] = []
    avoid: list[str] = []


class Finding(BaseModel):
    """One thing the literature says matters, with its provenance.

    Args:
        claim: What is claimed.
        source: Title, DOI or URL. A criterion without provenance is just an assertion.
        relevance: Why it bears on this goal.
    """

    claim: str
    source: str
    relevance: str


Brief.model_rebuild()


#: The builder and the critic do the reasoning, so they get the stronger model.
DEFAULT_REASONING_MODEL = "anthropic:claude-fable-5-1"

#: The verifier does not. Since Acceptance demoted it to a veto backed by deterministic
#: assertion checks, what it needs is independence from the builder, not depth: if one
#: model both writes the plan and judges it, a shared blind spot survives, and the
#: critique loop pushes the builder to satisfy that judge every round. Cheaper is a
#: bonus, since this one runs on every round.
DEFAULT_VERIFY_MODEL = "anthropic:claude-haiku-4-5"


class HypothesisConfig(BaseModel):
    """How hard to try, and with which models.

    Args:
        build_model: pydantic-ai model for the builder.
        verify_model: pydantic-ai model for the verifier. Use a different model from the
            builder: the critique loop pushes the builder to satisfy this judge, so a
            shared blind spot would compound every round.
        critique_model: pydantic-ai model for the critic.
        max_rounds: Plan-run-verify rounds before giving up.
        max_requests: New tools one plan may ask for.
        patience: Rounds without a better score before giving up.
        max_tokens: Total tokens this hypothesis may spend before stopping.
    """

    build_model: str = DEFAULT_REASONING_MODEL
    verify_model: str = DEFAULT_VERIFY_MODEL
    critique_model: str = DEFAULT_REASONING_MODEL
    max_rounds: int = Field(default=3, ge=1, le=10)
    max_requests: int = Field(default=2, ge=0, le=5)
    patience: int = Field(default=2, ge=1)
    max_tokens: int = Field(default=2_000_000, ge=1)


class HypothesisInput(HypothesisConfig):
    """Input to HypothesisWorkflow.

    Args:
        hypothesis: The goal, inputs and criteria. The workflow fills in the rest.
        proposed: The user's own idea of how to meet the goal, if any.
    """

    hypothesis: Hypothesis
    proposed: str | None = None


class CriteriaInput(BaseModel):
    """Input to derive_criteria.

    Args:
        goal: What the run must achieve.
        input_kinds: Each DAG input name to its kind.
        model: pydantic-ai model to use.
    """

    goal: str
    input_kinds: dict[str, str]
    model: str
    tag: str = ""


class AgentOutput(BaseModel):
    """What every agent activity reports about its own run.

    Args:
        trajectory: Where the message history was written, under ``results/trajectories``.
        tokens: Tokens this call used.
        error: Why the call produced nothing usable, if it did.
    """

    trajectory: str | None = None
    tokens: int = 0
    error: str | None = None


class CriteriaOutput(AgentOutput):
    """What derive_criteria returns.

    Args:
        criteria: What must be true for the goal to be met. Marked ``derived``.
        brief: What the literature said, once the research stage exists.
    """

    criteria: list[Criterion] = []
    brief: Brief | None = None


class PlanInput(BaseModel):
    """Input to plan_hypothesis.

    Carries everything the builder is allowed to reason from: the goal, the frozen
    criteria, a preview of the inputs, the latest critique, and **every** previous
    attempt.

    Args:
        goal: What the run must achieve.
        criteria: What must be true. The builder may not change these.
        input_kinds: Each DAG input name to its kind.
        input_preview: Each input name to a short description of its value, so a long
            sequence does not ride in the prompt every round.
        model: pydantic-ai model for the builder.
        proposed: The user's starting idea, if any.
        critique: Last round's critique. None on round 1.
        history: Every previous attempt, summarised.
        brief: What the literature said. Phase 2.
        max_requests: New tools this plan may ask for.
    """

    goal: str
    criteria: list[Criterion] = []
    input_kinds: dict[str, str]
    input_preview: dict[str, str] = {}
    model: str
    proposed: str | None = None
    critique: Critique | None = None
    history: list[AttemptSummary] = []
    brief: Brief | None = None
    max_requests: int = 2
    tag: str = ""


class PlanOutput(AgentOutput):
    """What plan_hypothesis returns. Exactly one of ``plan`` and ``error`` is set.

    Args:
        plan: The builder's plan.
        tools_called: The catalogue tools the builder called, in order.
    """

    plan: Plan | None = None
    tools_called: list[str] = []


class ResolveInput(BaseModel):
    """Input to resolve_plan.

    Args:
        plan: The plan to split into what this worker has and what it lacks.
        goal_inputs: Each DAG input name the hypothesis has, to its kind. A plan whose
            Dag declares anything else cannot be given its inputs, and saying so here
            beats letting DagInput reject it mid-round.
    """

    plan: Plan
    goal_inputs: dict[str, str] = {}


class ResolveOutput(BaseModel):
    """What this worker's registry can and cannot run of a plan.

    Args:
        available: The plan's node names this worker has.
        missing: A contract for each node it does not have, in plan order.
        mismatched: Nodes that now exist but whose real contract is not the one the plan
            was typechecked against, keyed by node name.
        dag: The built Dag, set only when nothing is missing or mismatched.
        error: Why the plan cannot become a Dag: a wiring fault or a bad config.
        registry_version: A hash of this worker's sorted node names. Unchanged across a
            resume means the worker was never restarted.
    """

    available: list[str] = []
    missing: list[ToolRequest] = []
    mismatched: dict[str, PortContract] = {}
    dag: Dag | None = None
    error: str | None = None
    registry_version: str


class VerifyView(BaseModel):
    """What the verifier is allowed to see.

    An allow-list, not an exclusion: once a Hypothesis carries ``attempts``, dumping it
    whole would hand the judge every previous verdict and critique, biasing it towards
    agreeing with itself.

    Args:
        goal: What was asked.
        criteria: What must be true for the goal to be met.
        inputs: The values the DAG ran on.
        hypothesis: What the builder claimed this plan would do.
        expected: What it predicted.
        assertions: The claims it committed to.
        held: Each assertion's source to whether its branch fired.
        dag: The DAG that ran.
        outcome: Every value it produced.
        error: Why the run failed, if it did.
    """

    goal: str
    criteria: list[Criterion] = []
    inputs: dict[str, Value]
    hypothesis: str
    expected: str
    assertions: list[Assertion] = []
    held: dict[str, bool] = {}
    dag: Dag | None = None
    outcome: DagOutput | None = None
    error: str | None = None


class VerifyInput(BaseModel):
    """Input to verify_outcome.

    Args:
        view: What the verifier judges.
        model: pydantic-ai model for the verifier.
    """

    view: VerifyView
    model: str
    tag: str = ""


class CritiqueInput(BaseModel):
    """Input to critique_attempt.

    Args:
        view: The goal, the criteria, the DAG and the outcome.
        verdict: Why the verifier said no. None when the run itself failed.
        history: The rounds already tried, so the critique does not send it back to one.
        brief: What the literature said. Phase 2; read from cache, never searched live.
        model: pydantic-ai model for the critic.
    """

    view: VerifyView
    verdict: Verdict | None = None
    history: list[AttemptSummary] = []
    brief: Brief | None = None
    model: str
    tag: str = ""


class CritiqueOutput(AgentOutput):
    """What critique_attempt returns. Exactly one of ``critique`` and ``error`` is set.

    Args:
        critique: Why the attempt missed and what to change.
    """

    critique: Critique | None = None


class SaveHypothesisInput(BaseModel):
    """Input to save_hypothesis_state.

    Args:
        hypothesis: The Hypothesis to write, so the pages can show how far it has got.
    """

    hypothesis: Hypothesis


class SaveRequestsInput(BaseModel):
    """Input to save_requests.

    Args:
        hypothesis_id: Which run is blocked.
        goal: Its goal, for the requests page.
        round: Which round blocked.
        requests: The contracts to write under ``results/requests``.
        mismatched: Nodes that exist but with the wrong contract.
    """

    hypothesis_id: str
    goal: str
    round: int
    requests: list[ToolRequest] = []
    mismatched: dict[str, PortContract] = {}


def preview(value: Value) -> str:
    """A short description of a value: its kind and shape, never its full contents.

    The builder needs to know that a step produced a 2970-base sequence, not what its
    bases were. Keeping full values out of the history is what holds the activity payload
    under Temporal's 2 MB limit after several rounds.

    Args:
        value: The value to describe.
    """
    data: dict[str, Any] = value.model_dump()
    if (seq := data.get("sequence")) is not None:
        if len(seq) <= 24:
            return f"{value.kind}, {len(seq)} long, {seq}"
        return f"{value.kind}, {len(seq)} long, {seq[:12]}…{seq[-6:]}"
    rest = {k: v for k, v in data.items() if k != "kind"}
    return f"{value.kind}, {rest}"
