import json
import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_ai import Agent, ModelRetry, RunContext, Tool, ToolOutput
from pydantic_ai.models import Model

from node_dag import amass
from node_dag.factory import MAPPING, NodeConfig
from node_dag.nodes.base import BaseFilterConfig, BaseNodeConfig
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.plan import (
    Attempt,
    Criterion,
    Critique,
    DraftObservation,
    HypothesisState,
    Observation,
    Plan,
    ToolRequest,
    VerifyOpinion,
    repeated,
)
from node_dag.registry import Registry
from node_dag.types import TYPES, Entity, Value

NODES = {c.model_fields["name"].default: c for c in MAPPING}
# What a file from before the loop kept on the Hypothesis, and each Attempt keeps now.
TOP_LEVEL_RUN = ("dag", "workflow_id", "outcome", "verdict")


class Hypothesis(BaseModel):
    """A goal, and the plans tried against it, round by round.

    Set ``goal`` and ``inputs``, and ``criteria`` or ``hypothesis`` if you have them.
    ``HypothesisLoop`` fills in the rest: ``attempts`` holds each round's plan, DAG,
    outcome and verdict, and ``current`` is the one in progress.

    Args:
        id: Names the saved file. Generated if not given.
        goal: What the DAG must do, in plain English, e.g. "lower the atom count of the sequences".
        inputs: The list of entities to run on, keyed by DAG input name. Each list is
            not empty and holds one kind. Empty when the caller gave none: the loop
            then takes them from the goal before round 1, and they stay as they are.
        input_sources: Where each input came from, in a phrase, e.g. "given in the
            goal". The only provenance a run carries.
        hypothesis: Your own idea of how to meet ``goal``, for the builder to take or leave.
        observations: The literature the run is built on: the records the user kept
            before the build, and each one the builder has cited since, with how it
            used it. A plan may cite any of them.
        criteria: What must be true for the goal to be met. Frozen before any plan.
        state: Where the loop has got to. None for a file from before the loop.
        round: The current round.
        attempts: Every round so far. Older rounds keep previews, not their outcome.
        usage: Tokens used, per stage and in ``total``.
        max_rounds: The most rounds this run may take, saved when it starts. None for a
            file from before the loop recorded it.
        max_tokens: The token ceiling, checked before each round. None as ``max_rounds``.
        stopped_because: Why the loop ended.
    """

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid.uuid4()}")
    goal: str
    inputs: dict[str, list[Value]] = {}
    input_sources: dict[str, str] = {}
    hypothesis: str | None = None
    observations: list[Observation] = []
    criteria: list[Criterion] = []
    state: HypothesisState | None = None
    round: int = 0
    attempts: list[Attempt] = []
    usage: dict[str, int] = {}
    max_rounds: int | None = None
    max_tokens: int | None = None
    stopped_because: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_before_the_loop(cls, data: Any) -> Any:  # noqa: ANN401
        """A file from before the loop kept its one run at the top: make it round 1."""
        if not isinstance(data, dict) or data.get("attempts"):
            return data
        # A copy: a before-validator must not change its caller's input.
        data = dict(data)
        run = {k: data.pop(k) for k in TOP_LEVEL_RUN if data.get(k)}
        if run:
            data |= {"round": 1, "attempts": [{"round": 1, **run}]}
        return data

    @field_validator("criteria")
    @classmethod
    def _check_criteria(cls, v: list[Criterion]) -> list[Criterion]:
        if dup := repeated(v):
            raise ValueError(f"Criterion ids must be unique; these repeat: {dup}")
        return v

    @field_validator("inputs")
    @classmethod
    def _check_inputs(cls, v: dict[str, list[Value]]) -> dict[str, list[Value]]:
        for name, items in v.items():
            if not items or len({i.kind for i in items}) != 1:
                raise ValueError(
                    f"Input {name!r} must be a list of one kind, not empty"
                )
        return v

    @property
    def current(self) -> Attempt | None:
        """This round's attempt. None while the builder is still planning it."""
        return next((a for a in self.attempts if a.round == self.round), None)

    @property
    def pending(self) -> list[ToolRequest]:
        """The nodes a blocked run is waiting for."""
        cur = self.current
        return cur.requests if cur and self.state == "blocked" else []

    def input_kinds(self) -> dict[str, str]:
        """The DAG inputs the builder must declare: name to the kind of its entities."""
        return {k: v[0].kind for k, v in self.inputs.items()}

    def describe_inputs(self, limit: int = 20) -> dict[str, dict[str, Any]]:
        """What the builder is shown of each input: its kind, size and first entities.

        The builder needs the values themselves to fill in config fields such as a
        reference sequence or a threshold.
        """
        return {
            k: {
                "kind": v[0].kind,
                "count": len(v),
                "sequences": [i.sequence for i in v[:limit]],
            }
            for k, v in self.inputs.items()
        }


def list_nodes() -> str:
    """List every node: name, categories, input ports, outputs and a summary."""
    # ponytail: lists every node. Add a category filter when the list is too long.
    return "\n".join(
        f"{name} {json.dumps(c.contract())}: {(c.__doc__ or '').splitlines()[0]}"
        for name, c in NODES.items()
    )


def search_nodes(
    query: str = "",
    input_type: str | None = None,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Search available nodes by intent query and filter by input entity type and category.

    Args:
        query: Words or phrase describing what you want to do (e.g. "score expression", "mutate", "lower atoms", "translate").
        input_type: Input entity kind to filter by ('dna', 'rna', 'amino_acid_sequence', 'protein_structure', 'protein_contacts', 'entity').
        category: Node category to filter by ('scoring', 'filter', 'generation', 'conversion').

    Returns a list of matching nodes with their intents, when to use them, and input/output contracts.
    """
    results: list[dict[str, Any]] = []
    query_tokens = [w.lower() for w in query.split()] if query else []

    for name, node_cls in sorted(NODES.items()):
        contract = node_cls.contract()
        cat_values = contract["categories"]

        # A port takes its kind and every kind under it, e.g. nucleic_acid takes dna.
        want = TYPES.get(input_type.lower(), Entity) if input_type else None
        if want and not node_cls.takes(want):
            continue

        if category and category.lower() not in cat_values:
            continue

        score = 0
        if query_tokens:
            intents_text = " ".join(contract.get("intents", [])).lower()
            when_text = contract.get("when_to_use", "").lower()
            doc_text = (node_cls.__doc__ or "").lower()
            name_text = name.lower()

            for token in query_tokens:
                if token in name_text:
                    score += 5
                if token in intents_text:
                    score += 3
                if token in when_text:
                    score += 2
                if token in doc_text:
                    score += 1
                if token in cat_values:
                    score += 2
            if score == 0:
                continue

        results.append(
            {
                "score": score,
                "data": {
                    "name": name,
                    "categories": contract["categories"],
                    "input": contract["inputs"],
                    "outputs": contract["outputs"],
                    "intents": contract.get("intents", []),
                    "when_to_use": contract.get("when_to_use", ""),
                    "when_not_to_use": contract.get("when_not_to_use", ""),
                },
            }
        )

    results.sort(key=lambda r: r["score"], reverse=True)
    return [r["data"] for r in results]


def describe_node(name: str) -> dict[str, Any]:
    """Return the config schema of one node: its fields, docs, ports and outputs.

    Args:
        name: A node name from list_nodes.
    """
    if name not in NODES:
        raise ModelRetry(f"Unknown node {name!r}. Known nodes: {sorted(NODES)}")
    return NODES[name].model_json_schema()


# What search_literature shows of each hit. The cache in results/amass keeps it all.
_HIT_FIELDS = (
    "amassId",
    "title",
    "abstract",
    "journal",
    "publicationDate",
    "citationCount",
    "doi",
    "url",
)


def _brief(core: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the agent is shown of each search hit."""
    # Other cores name their fields differently, so only trim the ones we know.
    if core != "biomedcore":
        return hits
    return [{f: h[f] for f in _HIT_FIELDS if f in h} for h in hits]


# The Amass records an agent was shown, by amassId, each with the core it is in.
Seen = dict[str, tuple[str, dict[str, Any]]]


def _literature_tools(seen: Seen | None = None) -> tuple[Seen, list[Tool]]:
    """The Amass search tools, and every record they have shown, by amassId.

    An observation must cite a record in the returned dict, so an agent cannot cite
    one it made up. Pass ``seen`` to read what the tools showed afterwards; each agent
    needs its own, since what one was shown does not license another's citation.
    """
    seen = {} if seen is None else seen

    def search_literature(
        query: str, core: amass.Core = "biomedcore", limit: int = 5
    ) -> list[dict[str, Any]] | dict[str, str]:
        """Search Amass for publications or other records about a topic.

        Use it to ground a choice in what is known: e.g. a typical expression level, a
        sensible threshold, or which measure suits the goal. A query asked before is
        answered from the cache.

        Args:
            query: What to look for, in plain words, e.g. "Shine-Dalgarno spacing translation initiation".
            core: biomedcore (publications), trialcore (clinical trials), drugcore
                (drugs), regulatorycore (FDA and EMA approvals), genecore (genes) or
                patentcore (patents).
            limit: How many records to return, at most.
        """
        try:
            hits = amass.search(core, query, limit)
        except amass.AmassError as e:
            return {"error": str(e)}
        seen.update({h["amassId"]: (core, h) for h in hits if "amassId" in h})
        return _brief(core, hits)

    def get_record(
        amass_id: str, core: amass.Core = "biomedcore", include: list[str] | None = None
    ) -> dict[str, Any]:
        """Fetch one Amass record in full, by the amassId that search_literature gave.

        Args:
            amass_id: The record's amassId, e.g. ``AMBC_...``.
            core: The core the record came from.
            include: Extra fields to add, e.g. ``["fulltext"]``. Full text is long: ask
                for it only when the abstract is not enough.
        """
        try:
            record = amass.get_record(core, amass_id, tuple(include or ()))
        except amass.AmassError as e:
            return {"error": str(e)}
        seen[amass_id] = (core, record)
        return record

    return seen, [Tool(search_literature), Tool(get_record)]


REQUESTS_ALLOWED = """\
If no existing node can do a step, put its contract in `requests`, keyed by name, and use
   that name in a step. Name the existing nodes you considered in why_not_composable. A
   filter on a requested scorer's column gets that column name from the error you are shown."""

REQUESTS_REFUSED = """\
Every step must use a node that already exists: `requests` is disabled, and a plan
   carrying one is sent back. If no node seems to fit, look again with search_nodes and
   describe_node, since a node's config often covers a case its summary does not name."""


def build_instructions(allow_requests: bool) -> str:
    """The builder's instructions, with step 4 saying whether a node may be requested."""
    return BUILD_TEMPLATE.replace(
        "{requests_step}", REQUESTS_ALLOWED if allow_requests else REQUESTS_REFUSED
    )


BUILD_TEMPLATE = f"""\
Plan a DAG of nodes that meets the user's goal. You are shown the input entities and the
criteria you will be marked against, which you cannot change. An entity is any kind the
framework knows, not only a nucleic acid: {", ".join(sorted(TYPES))}. Plan on the kinds
you were actually given, and check a node's port accepts that kind before using it. A
config field such as a reference entity or a threshold must be a real value from those
inputs, never a placeholder.

Shapes that usually fit a goal:
- Measure or convert given entities: input -> scorer or converter.
- Screen entities against a threshold: input -> scorer -> filter.
- Find, improve, raise or lower something: input -> generator (e.g. mutate_synonymous or
  recode_targeted) -> scorer -> filter. Scoring and filtering alone cannot find what the
  inputs do not already hold. Work out the inputs' baseline score and set the threshold
  relative to it: a threshold that lets through entities at or below the baseline decides
  nothing.

The prompt may list observations: records found in the literature for this goal before
the build, which the user kept. Treat them as what is already known about the goal. Use
them where they bear on a choice such as a threshold, a measure or which sequences to run
on; get_record reads one in full. They are yours to cite in the plan's observations.
Where they are silent on a choice, search for yourself, and where they do not settle it,
say so in your hypothesis rather than overstating them.

1. Call search_nodes (by intent and input_type) or list_nodes, list_registry for nodes
   already made, and describe_node for each kind you use. When a choice depends on
   biology you are unsure of, such as a threshold or which measure fits the goal, call
   search_literature, and get_record for more of a hit.
2. Each step names a registered node id (from create_node), or a node name with its fields
   in `config`. Connect its input port to a source whose kind is the kind of that port.
   Register a scorer with create_node before the filter on its column, and copy the column
   name from the reply.
3. Every criterion needs an assertion on a step, and one check may be asserted for each
   criterion it covers. On a filter, say "yes" if every entity must pass it (the assertion
   holds only if the yes branch took every entity and the no branch none), or "no" for the
   reverse. When every candidate is expected to pass, assert "yes" on the filter itself.
   On a filter, "produced" shows only that it kept at least one entity: it never covers a
   criterion about what the kept entities hold, such as "every kept sequence scores above
   the first". When some of a pool fail the filter, hold such a criterion by putting the
   filter's yes branch (`<filter>.yes`) through a second filter that every kept entity must
   pass, and assert "yes" on that one. beats_reference reads the baseline from the run, so
   no number is copied. Never assert both yes and no of one filter: they cannot both hold.
   On any other step, "produced" holds when the step gave output, which is all a goal that
   only measures or converts needs.
4. {{requests_step}}
5. After a rejected round, say in addresses_critique what changed, and do not resubmit a
   wiring that already ran.
6. Add an observation for each record that bears on the plan, whether you searched for it
   or the prompt listed it: its amassId and a summary of what it found and how that
   shaped the plan.
Every source is a list of entities, and a node runs once on the whole list that reaches it.
- A tool step makes new entities, under its key. They have no scores.
- A scoring step passes its entities on under its key, and adds its score columns.
- A filter step splits its entities into <step>.yes and <step>.no by its `column`.
A kind refuses anything outside its alphabet (dna holds only A, C, G, T), so an alphabet
criterion on DNA holds by type: "produced" on the step that makes the DNA is enough for it,
and no node is needed to check it. Any other part of the criterion (length, start or stop
codon) needs an assertion on a node that measures it; if none does, request one.
Known kinds: {sorted(TYPES)}."""

VERIFY_INSTRUCTIONS = """\
You get JSON: a goal, its criteria and inputs, the plan (hypothesis, expected, assertions),
`held` (whether each assertion held, worked out by code: "yes" or "no" needs that branch to
take every entity and the other none, so false does not mean the branch took nothing;
"produced" needs the step, or the filter's yes branch, to give at least one entity),
`nodes` (what each node in the DAG says it does), the DAG and the outcome.
Work out the expected result from the goal and inputs yourself, and do not trust the plan.
Set agrees to true only if the outcome holds the expected result for every input. Set
covers_goal to true only if the assertions genuinely test every criterion. Do not claim
to have checked by eye what no assertion covers: say in reason what went unchecked, and set
covers_goal to false if any part of a criterion went unchecked. A
"produced" assertion only shows its step gave output, or a filter kept something: set
covers_goal to false if the criterion is about what that output holds, such as the
filter's threshold being right. A "yes" assertion that held, on a filter that measures it
(such as beats_reference against the baseline) covers a criterion about what is kept
only if the filter's bar is the bar the criterion names, in its direction and strictness
(read it in the DAG): a repeat of a filter on its own yes branch holds by construction and
adds nothing to the first filter's bar. A type guarantees its own alphabet, so "produced"
on the step that makes DNA covers an alphabet criterion on DNA, and only that: length,
start codon and stop codon are not guaranteed by type. Take what a node does
from `nodes`, not from a guess:
do not say a node returns its inputs unchanged unless `nodes` says it can. You cannot declare success: false is a veto and
true grants nothing.
Set agrees to false, whatever else the DAG did, when:
- The goal names a measure, a method or a node that the DAG did not use. A different
  node is not a substitute, and the hypothesis calling it one does not make it one.
- The goal asks to find or choose something, and the DAG chose nothing: it returns its
  inputs unchanged, or none passed the filter that makes the choice.
- The goal asks for higher, lower, more, less or optimized values, but the outcome holds
  only the original inputs, without improvement.
- A threshold let through entities the goal says to leave out, such as ones at or below
  the baseline, so the filter decided nothing. Every entity passing is no fault when none
  is one the goal says to leave out, as when each clears the baseline. Equal to the
  baseline does not clear it."""

CRITIQUE_INSTRUCTIONS = """\
You get a round that missed its goal as JSON: the plan, the outcome, the verdict and earlier
rounds. Say what went wrong in terms of its steps and values, the one root cause, which
step keys were right (keep), and what the next plan must do differently. Do not send it
back to a wiring an earlier round already ran. If no available node can check or do
something the goal needs, say so with root_cause "missing_tool" and name the tool in fix:
it becomes a request for a person to write that node. Use "missing_tool" for a need that no
node meets, not for a result you did not expect. Before you say a node misbehaves, read what
it does in `nodes` and check the claim against the outcome's values. Entities with the same
sequence are one entity, so a variant can equal an input without being a copy of it. Blame a
threshold only when the values show it let through entities the goal says to leave out,
such as ones at or below the baseline: every entity passing is no fault when none is one the
goal says to leave out, as when each clears the baseline. Equal to the baseline does not
clear it."""

CRITERIA_INSTRUCTIONS = """\
Turn the goal into one to four criteria that decide whether it was met. Each is a claim a
filter over the DAG's output could check. State what must be true, not how to do it.
Mark each quantitative when it names a measure or a comparison, qualitative when it
states a property to judge."""

COMPARATIVE_WORDS = (
    "higher",
    "lower",
    "reduce",
    "decrease",
    "increase",
    "fewer",
    "less",
    "more",
    "optimize",
    "better",
    "beat",
    "minimize",
    "maximize",
)
MAX_REQUESTS = 3
MAX_IDS_LISTED = 20  # Of the registered ids an unknown node's message names.
ADAPTER: TypeAdapter[NodeConfig] = TypeAdapter(NodeConfig)


def step_config(plan: Plan, key: str, registry: Registry) -> BaseNodeConfig:
    """The config of step ``key``: registered, an existing node, or a requested stand-in."""
    s = plan.steps[key]
    if node := registry.get(s.node):
        return node.config
    requested = s.node in plan.requests and s.node not in NODES
    if not requested and s.node not in NODES:
        ids = sorted(n.id for n in registry.all())
        more = (
            f" and {len(ids) - MAX_IDS_LISTED} more"
            if len(ids) > MAX_IDS_LISTED
            else ""
        )
        raise ValueError(
            f"Step {key!r}: node {s.node!r} is not a registered node id, a built-in "
            f"node name or a request. Registered ids: "
            f"{ids[:MAX_IDS_LISTED]}{more} (list_registry shows them). "
            f"Built-in names: {sorted(NODES)}. A node that does not exist yet goes in "
            "`requests`, under the name the step uses."
        )
    try:
        if requested:
            return plan.requests[s.node].stand_in()(**s.config)
        return ADAPTER.validate_python({"name": s.node, **s.config})
    except ValidationError as e:
        # Not str(e): its first line is every node's type, and the step is never named.
        found = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'] if p != s.node) or 'config'}: {err['msg']}"
            for err in e.errors()
        )
        where = (
            "a requested node's fields are its config_fields, plus `column` for a filter"
            if requested
            else f"describe_node {s.node} lists the fields"
        )
        raise ValueError(
            f"Step {key!r} ({s.node}) has a config that does not fit it: {found}. "
            f"Put the fields in the step's `config`; {where}."
        ) from e


def plan_prompt(hyp: Hypothesis) -> str:
    """What the builder reasons from: goal, inputs, criteria, and every attempt so far."""
    parts = [f"Goal: {hyp.goal}", f"Inputs: {json.dumps(hyp.describe_inputs())}"]
    parts.append(
        "Criteria (each needs an assertion): "
        + json.dumps({c.id: c.claim for c in hyp.criteria})
    )
    if hyp.hypothesis:
        parts.append(f"The user's own idea, to take or leave: {hyp.hypothesis}")
    if (
        hyp.observations
    ):  # Kept by the user before the build, or cited in an earlier round.
        parts.append(
            "Observations:\n"
            + "\n".join(
                f"- [{o.core} {o.amass_id}] {o.title}: {o.summary}"
                for o in hyp.observations
            )
        )
    if hyp.attempts:
        parts.append(
            "Attempts so far:\n"
            + json.dumps([a.summary() for a in hyp.attempts], indent=1)
        )
    return "\n\n".join(parts)


def cite(
    plan: Plan, seen: Seen, given: Sequence[Observation] = ()
) -> list[Observation]:
    """The observations after this plan: those ``given`` it, and each record it cites.

    A record the prompt listed keeps the summary the user kept and the title and link it
    carried: the plan's text says how it used the record, and is kept in ``used``. A
    record only the plan found is filled in from ``seen``. A record that went uncited
    stays, so a record gathered before the build is not lost by going uncited.
    """
    listed = {g.amass_id: g for g in given}
    cited = {
        o.amass_id: (
            listed[o.amass_id].model_copy(update={"used": o.summary})
            if o.amass_id in listed
            else Observation.from_record(o, *seen[o.amass_id])
        )
        for o in plan.observations
    }
    return [cited.pop(g.amass_id, g) for g in given] + list(cited.values())


OBSERVE_INSTRUCTIONS = """\
You get a goal for a computational experiment on biological entities, and sometimes a
proposed hypothesis. Search the literature for what is already known that bears on it,
and submit the records worth building on. The builder agent is shown your list and uses
it to choose its nodes, its thresholds and the entities to run on, and the user reviews
and edits the list first, so make it a draft worth correcting.

- Search with search_literature. Run a few queries, not one: the subject the goal
  names, the measure it asks for, and the method it implies. Call
  get_record for a hit whose abstract is not enough to tell what it found.
- Submit two to six records. Prefer one that pins down a number the builder will
  have to pick, e.g. a typical expression level or a sensible threshold, over one
  that is merely on topic.
- Each summary says what that record found, and what it implies for building this
  DAG. Keep it to what the record supports: say the record measured one organism,
  or used a proxy, where it did. A record that would change the build only if it
  generalises is worth submitting with that doubt stated.
- Submit nothing rather than a record you cannot tell is relevant. An empty list
  is a usable answer: it says the literature searched did not settle the choices.
- Cite only records that search_literature or get_record showed you."""


def build_agent(
    model: Model | str,
    registry: Registry,
    seen: Seen | None = None,
    *,
    allow_requests: bool = False,
) -> Agent[Hypothesis, Plan]:
    """Return an agent that writes a Plan for a goal, with each guard a retry.

    The agent makes the nodes it needs in ``registry`` with create_node, and can reuse
    the ones already there. Run it with ``deps=`` the Hypothesis, whose ``criteria`` and
    ``attempts`` the guards read.

    With ``allow_requests``, a plan may name a node that does not exist, if it carries a
    ToolRequest for it; the plan is typechecked as if the node were written, and the loop
    blocks until a person writes it. It is off by default, so a plan carrying a request is
    sent back and the agent has to compose the step from the nodes that exist.

    The agent can search the literature, and a plan may only cite records it was shown.
    Pass an empty ``seen`` to read them afterwards, for :func:`cite`.
    """
    seen = {} if seen is None else seen

    def create_node(config: dict[str, Any], description: str) -> dict[str, Any]:
        """Make a node and add it to the registry. Make nodes one at a time.

        Returns the node's id, config, input port, outputs and score columns. If this
        config is already registered, returns that node with ``new`` false.

        Args:
            config: ``{"name": <node name>, <field>: <value>, ...}``. Read the fields
                with describe_node. Leave out config_hash.
            description: What this node is for in this DAG, in a sentence.
        """
        try:
            node, new = registry.register(ADAPTER.validate_python(config), description)
        except (ValidationError, ValueError) as e:
            raise ModelRetry(str(e)) from e
        return {**node.summary(), "new": new}

    def list_registry() -> list[dict[str, Any]]:
        """List every registered node: id, description, config, input, outputs, score columns."""
        return [n.summary() for n in registry.all()]

    _, literature = _literature_tools(seen)

    def check_plan(ctx: RunContext[Hypothesis], plan: Plan) -> Plan:
        hyp, reqs = ctx.deps, plan.requests
        if plan.inputs != hyp.input_kinds():
            raise ModelRetry(
                f"inputs must be exactly the goal's inputs: {hyp.input_kinds()}"
            )
        given = {o.amass_id for o in hyp.observations}
        if unseen := sorted(
            {o.amass_id for o in plan.observations} - seen.keys() - given
        ):
            raise ModelRetry(
                f"Observations cite records you were not shown: {unseen}. Cite only "
                "amassIds from search_literature, get_record or the prompt's "
                f"observations: {sorted(seen.keys() | given)}"
            )
        if reqs and not allow_requests:
            raise ModelRetry(
                f"Requesting a node is disabled, but this plan requests {sorted(reqs)}. "
                "Compose the step from the nodes that exist, which search_nodes and "
                f"describe_node list: {sorted(NODES)}. A node's config is often wider "
                "than its summary, so read the fields before ruling it out."
            )
        used = {s.node for s in plan.steps.values()}
        if len(reqs) > MAX_REQUESTS or (reqs and used <= reqs.keys()):
            raise ModelRetry(
                f"Request at most {MAX_REQUESTS} nodes, and not for every step."
            )
        if unused := sorted(reqs.keys() - used):
            raise ModelRetry(f"Requests no step uses: {unused}")
        for r in reqs.values():
            if r.name in NODES:
                raise ModelRetry(f"{r.name} already exists: use it, do not request it.")
            if not any(n in r.why_not_composable for n in NODES):
                raise ModelRetry(
                    f"{r.name}: why_not_composable must name the existing nodes you considered: {sorted(NODES)}"
                )
        try:
            configs = {k: step_config(plan, k, registry) for k in plan.steps}
        except (ValidationError, ValueError, TypeError) as e:
            raise ModelRetry(str(e)) from e
        if (
            any(w in hyp.goal.lower() for w in COMPARATIVE_WORDS)
            and sum(len(v) for v in hyp.inputs.values()) == 1
            and not any(
                "generation" in [c.value for c in cfg.categories]
                for cfg in configs.values()
            )
        ):
            raise ModelRetry(
                f"The goal asks to optimize or find improved sequences ({hyp.goal!r}), "
                "but only 1 input sequence was provided and the DAG has no generation node "
                "(e.g. mutate_synonymous) to create candidate variants. A DAG that only "
                "measures the input cannot find sequences with higher/lower metrics. "
                "Add a generation step (e.g. mutate_synonymous with variants_per_sequence > 1) "
                "to generate variants, score them, and filter for those that beat the baseline."
            )
        for step, cfg in configs.items():
            if (
                isinstance(cfg, AtLeastConfig)
                and "expression" in cfg.column
                and cfg.threshold <= 0.0
            ):
                raise ModelRetry(
                    f"Step {step!r} filters on expression with threshold {cfg.threshold}, "
                    "which allows every sequence to pass trivially. Determine the baseline score "
                    "and set a threshold that requires candidates to beat the baseline."
                )
        try:
            plan.typecheck(configs)
        except (ValidationError, ValueError, TypeError) as e:
            raise ModelRetry(str(e)) from e
        inputs = {i.id: i for items in hyp.inputs.values() for i in items}
        for step, cfg in configs.items():
            if not (
                isinstance(cfg, BaseFilterConfig) and (ref := cfg.reads_reference())
            ):
                continue
            scored_in, ref_id = ref
            # A reference that is no input is never scored, and fails the run later.
            if ref_id not in inputs:
                shown = [
                    f"{i.kind} {i.sequence[:20]}" for i in list(inputs.values())[:20]
                ]
                raise ModelRetry(
                    f"Step {step!r}: the reference {ref_id!r} must be one of the input "
                    f"entities: {shown}. It must be scored in step {scored_in!r} (its "
                    "scored_in) by the same node as the entities."
                )
            # Nor is one that no input the scored_in step reads holds. A tool can make
            # any entity, so past a tool the run decides.
            read = plan.dag(configs).inputs_read(scored_in)
            if read is not None and not any(
                ref_id == e.id for n in read for e in hyp.inputs[n]
            ):
                holders = sorted(
                    n
                    for n, items in hyp.inputs.items()
                    if any(e.id == ref_id for e in items)
                )
                raise ModelRetry(
                    f"Step {step!r}: the reference {inputs[ref_id].sequence[:20]!r} is in "
                    f"input {holders}, but its scored_in step {scored_in!r} reads only "
                    f"input {sorted(read)}, so it has no score for the reference. Score "
                    f"the reference with the same node as the entities in a step that "
                    f"reads {holders[0]!r}, and set scored_in to that step."
                )
        ids = {c.id for c in hyp.criteria}
        for a in plan.assertions:
            if a.criterion not in ids:
                raise ModelRetry(
                    f"Assertion on unknown criterion {a.criterion!r}; criteria: {sorted(ids)}"
                )
            if (cfg := configs.get(a.step)) is None:
                raise ModelRetry(
                    f"Assertion step {a.step!r} is not a step of the plan."
                )
            if not isinstance(cfg, BaseFilterConfig) and a.branch != "produced":
                raise ModelRetry(
                    f"Assertion step {a.step!r} is not a filter: use branch produced. "
                    "Branch yes or no is for a filter step."
                )
        sides = {(a.step, a.branch) for a in plan.assertions}
        if both := sorted(s for s, b in sides if b == "yes" and (s, "no") in sides):
            raise ModelRetry(
                f"Assertions on both branches of {both} cannot both hold: a filter that "
                "takes every entity down one branch takes none down the other."
            )
        if clash := sorted(
            s for s, b in sides if b == "no" and (s, "produced") in sides
        ):
            raise ModelRetry(
                f"Assertions no and produced on {clash} cannot both hold: no needs the "
                "filter's yes branch to be empty, and produced needs it to keep at "
                "least one. Keep one, or put the other on a second filter."
            )
        if uncovered := sorted(ids - {a.criterion for a in plan.assertions}):
            raise ModelRetry(f"No assertion covers {uncovered}.")
        last = hyp.attempts[-1] if hyp.attempts else None
        if plan.fingerprint() in {a.plan.fingerprint() for a in hyp.attempts if a.plan}:
            raise ModelRetry("This wiring already ran in an earlier round. Change it.")
        if last and last.critique and not plan.addresses_critique:
            raise ModelRetry(
                "Say in addresses_critique what this plan changes in response to the critique."
            )
        return plan

    agent = Agent(
        model,
        deps_type=Hypothesis,
        instructions=build_instructions(allow_requests),
        tools=[
            Tool(list_nodes),
            Tool(search_nodes),
            Tool(describe_node),
            Tool(list_registry),
            Tool(create_node),
            *literature,
        ],
        # Not strict: strict output sets additionalProperties false on every object,
        # which leaves steps, inputs, config and requests only able to be {}.
        output_type=ToolOutput(Plan, strict=False),
        retries={"output": 3},
    )
    agent.output_validator(check_plan)
    return agent


def verify_agent(model: Model | str) -> Agent[None, VerifyOpinion]:
    """Return an agent that judges a round. Its opinion can veto, never grant."""
    return Agent(model, instructions=VERIFY_INSTRUCTIONS, output_type=VerifyOpinion)


def critique_agent(model: Model | str) -> Agent[None, Critique]:
    """Return an agent that says why a round missed and what the next plan must change."""
    return Agent(model, instructions=CRITIQUE_INSTRUCTIONS, output_type=Critique)


_INPUTS_CORE = f"""\
Find the entities a goal is about, so that a DAG can be planned on them. An entity is
any kind the framework knows, not only a nucleic acid: {", ".join(sorted(TYPES))}.
Pass each to add_input as an object with its `kind` and that kind's fields, copied
exactly from the goal or the proposed hypothesis. Never invent an entity, and never
fill a gap with a plausible-looking sequence or structure. Add every input the goal
needs, name each for what it holds, say where it came from in `source`, and keep one
kind per input. Stop when they are all added."""

INPUTS_INSTRUCTIONS = (
    _INPUTS_CORE
    + """
You have no database or network tools: every entity must come from the goal or the
proposed hypothesis. If the goal names something whose value it does not give, say so
plainly and add nothing for it, rather than writing out a sequence from memory."""
)


class FoundInputs(BaseModel):
    """What the inputs agent added: the inputs, and where each came from."""

    inputs: dict[str, list[Value]] = {}
    sources: dict[str, str] = {}


def inputs_agent(model: Model | str) -> tuple[Agent[None, str], FoundInputs]:
    """Return an agent that finds a goal's input entities, and what it adds.

    It has one tool and no network: the entities come from the goal or the proposed
    hypothesis, whatever kind they are. A gene set to benchmark on is a committed file,
    read through ``node_dag.genes``, not something an agent fetches.
    """
    found = FoundInputs()
    value_adapter: TypeAdapter[Value] = TypeAdapter(Value)

    def add_input(
        name: str,
        source: str,
        entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Give the DAG an input: a named, non-empty list of entities of one kind.

        Give each entity as an object with its ``kind`` and that kind's own fields, so
        any kind the framework knows can be an input, not only a bare sequence. Pass
        only entities the goal or the proposed hypothesis gives you, copied exactly.

        Args:
            name: What the DAG calls this input, e.g. ``seq``. No dots.
            source: Where these entities came from, in a phrase, e.g. "given in the
                goal" or "the structure in the hypothesis". This is recorded.
            entities: The entities, each ``{"kind": ..., "sequence": ...}`` plus
                whatever else its kind needs, e.g. ``structure`` for a
                protein_structure.
        """
        if "." in name or not name.strip():
            raise ModelRetry(
                f"Input name {name!r} must be a non-empty name with no dots."
            )
        items: list[Value] = []
        for i, e in enumerate(entities or ()):
            if not isinstance(e, dict) or "kind" not in e:
                raise ModelRetry(
                    f"Entity {i} of {name!r} needs a `kind`. Known kinds: {sorted(TYPES)}."
                )
            if e["kind"] not in TYPES:
                raise ModelRetry(
                    f"Unknown kind {e['kind']!r}. Known kinds: {sorted(TYPES)}."
                )
            # Every kind writes its residues upper case, and nothing else is touched:
            # a structure's own fields are passed through as the model gave them.
            if isinstance(e.get("sequence"), str):
                e = {**e, "sequence": e["sequence"].strip().upper()}
            try:
                items.append(value_adapter.validate_python(e))
            except ValidationError as err:
                raise ModelRetry(
                    f"Entity {i} of {name!r} is not valid {e['kind']}: {err}"
                ) from err
        if not items:
            raise ModelRetry(f"Input {name!r} needs at least one entity.")
        if len(kinds := {i.kind for i in items}) > 1:
            raise ModelRetry(
                f"Input {name!r} holds one kind, not {sorted(kinds)}. Use one input per kind."
            )
        found.inputs[name] = items
        found.sources[name] = source
        return {
            "name": name,
            "kind": items[0].kind,
            "count": len(items),
            "lengths": [len(i.sequence) for i in items],
            "source": source,
        }

    agent = Agent(
        model,
        instructions=INPUTS_INSTRUCTIONS,
        tools=[Tool(add_input, max_retries=3)],
    )
    return agent, found


def criteria_agent(model: Model | str) -> Agent[None, list[Criterion]]:
    """Return an agent that turns a goal into the criteria that decide whether it was met."""
    agent = Agent(
        model, instructions=CRITERIA_INSTRUCTIONS, output_type=list[Criterion]
    )

    @agent.output_validator
    def unique(criteria: list[Criterion]) -> list[Criterion]:
        if dup := repeated(criteria):
            raise ModelRetry(f"Criterion ids must be unique; these repeat: {dup}.")
        return criteria

    return agent


def observations_agent(model: Model | str) -> Agent[None, list[Observation]]:
    """Return an agent that gathers the literature on a goal, for the user to edit.

    Run it before the builder, on the goal in words: it searches Amass and returns
    the records that bear on the goal. Put the list the user keeps on
    ``Hypothesis.observations``, and ``run_hypothesis`` shows it to the builder.
    """
    seen, literature = _literature_tools()

    def submit_observations(observations: list[DraftObservation]) -> list[Observation]:
        """Submit the records that bear on the goal, one observation per record.

        Args:
            observations: The findings, each citing a record search_literature or
                get_record showed you. Empty if the search found nothing that bears
                on the goal.
        """
        if unseen := sorted({o.amass_id for o in observations} - seen.keys()):
            raise ModelRetry(
                f"Observations cite records you were not shown: {unseen}. Cite only "
                f"amassIds from search_literature or get_record: {sorted(seen)}"
            )
        return [Observation.from_record(o, *seen[o.amass_id]) for o in observations]

    return Agent(
        model,
        instructions=OBSERVE_INSTRUCTIONS,
        tools=literature,
        output_type=submit_observations,
        retries={"output": 3},
    )
