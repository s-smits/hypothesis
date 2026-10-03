import json
import uuid
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

from node_dag import amass, entrez
from node_dag.factory import MAPPING, NodeConfig
from node_dag.nodes.base import BaseFilterConfig, BaseNodeConfig
from node_dag.plan import (
    Attempt,
    Criterion,
    Critique,
    HypothesisState,
    Observation,
    Plan,
    ToolRequest,
    VerifyOpinion,
    repeated,
)
from node_dag.registry import Registry
from node_dag.types import TYPES, Dna, Entity, Value

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
            then fetches them from NCBI before round 1, and they stay as they are.
        input_sources: Where each input the loop fetched came from, in a phrase, e.g.
            the NCBI record a CDS was taken from.
        hypothesis: Your own idea of how to meet ``goal``, for the builder to take or leave.
        observations: What the builder found in the literature that bears on the
            current round's plan.
        criteria: What must be true for the goal to be met. Frozen before any plan.
        state: Where the loop has got to. None for a file from before the loop.
        round: The current round.
        attempts: Every round so far. Older rounds keep previews, not their outcome.
        usage: Tokens used, per stage and in ``total``.
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
        input_type: Input entity kind to filter by ('dna', 'rna', 'amino_acid_sequence', 'protein_structure', 'entity').
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


BUILD_INSTRUCTIONS = f"""\
Plan a DAG of nodes that meets the user's goal. You are shown the input sequences and the
criteria you will be marked against, which you cannot change. A config field such as a
reference sequence or a threshold must be a real value from those inputs, never a placeholder.

Shapes that usually fit a goal:
- Measure or convert given sequences: input -> scorer or converter.
- Screen sequences against a threshold: input -> scorer -> filter.
- Find, improve, raise or lower something: input -> generator (e.g. mutate_synonymous or
  recode_targeted) -> scorer -> filter. Scoring and filtering alone cannot find what the
  inputs do not already hold. Work out the inputs' baseline score and set the threshold
  relative to it: a threshold every entity passes decides nothing.

1. Call search_nodes (by intent and input_type) or list_nodes, list_registry for nodes
   already made, and describe_node for each kind you use. When a choice depends on
   biology you are unsure of, such as a threshold or which measure fits the goal, call
   search_literature, and get_record for more of a hit.
2. Each step names a registered node id (from create_node), or a node name with its fields
   in `config`. Connect its input port to a source whose kind is the kind of that port.
   Register a scorer with create_node before the filter on its column, and copy the column
   name from the reply.
3. Every criterion needs an assertion: a filter step and the branch, yes or no, that proves
   it. The assertion holds only if that branch took every entity and the other took none.
   Never assert both branches of one filter: they cannot both hold. A goal that only asks
   to measure or convert has nothing to filter: assert branch "produced" on the tool or
   score step, which holds when that step gave output.
4. If no existing node can do a step, put its contract in `requests`, keyed by name, and use
   that name in a step. Name the existing nodes you considered in why_not_composable. A
   filter on a requested scorer's column gets that column name from the error you are shown.
5. After a rejected round, say in addresses_critique what changed, and do not resubmit a
   wiring that already ran.
6. If you searched the literature, add an observation for each record that bears on the
   plan: its amassId and a summary of what it found and how that shaped the plan.
Every source is a list of entities, and a node runs once on the whole list that reaches it.
- A tool step makes new entities, under its key. They have no scores.
- A scoring step passes its entities on under its key, and adds its score columns.
- A filter step splits its entities into <step>.yes and <step>.no by its `column`.
Known kinds: {sorted(TYPES)}."""

VERIFY_INSTRUCTIONS = """\
You get JSON: a goal, its criteria and inputs, the plan (hypothesis, expected, assertions),
`held` (whether each assertion's branch took every entity and the other took none, worked
out by code: false does not mean the branch took nothing), the DAG and the outcome.
Work out the expected result from the goal and inputs yourself, and do not trust the plan.
Set agrees to true only if the outcome holds the expected result for every input. Set
covers_goal to true only if the assertions genuinely test every criterion. Do not claim
to have checked by eye what no assertion covers: say in reason what went unchecked. A
"produced" assertion only shows its step gave output: set covers_goal to false if the
criterion is about what that output holds. You cannot declare success: false is a veto and
true grants nothing.
Set agrees to false, whatever else the DAG did, when:
- The goal names a measure, a method or a node that the DAG did not use. A different
  node is not a substitute, and the hypothesis calling it one does not make it one.
- The goal asks to find or choose something, and the DAG chose nothing: it returns its
  inputs unchanged, or every entity passed its filter, or none did.
- The goal asks for higher, lower, more, less or optimized values, but the outcome holds
  only the original inputs, without improvement.
- A threshold let everything through, so the filter decided nothing."""

CRITIQUE_INSTRUCTIONS = """\
You get a round that missed its goal as JSON: the plan, the outcome, the verdict and earlier
rounds. Say what went wrong in terms of its steps and values, the one root cause, which
step keys were right (keep), and what the next plan must do differently. Do not send it
back to a wiring an earlier round already ran. If no available node can check or do
something the goal needs, say so with root_cause "missing_tool" and name the tool in fix:
it becomes a request for a person to write that node."""

CRITERIA_INSTRUCTIONS = """\
Turn the goal into one to four criteria that decide whether it was met. Each is a claim a
filter over the DAG's output could check. State what must be true, not how to do it."""

MAX_REQUESTS = 3
ADAPTER: TypeAdapter[NodeConfig] = TypeAdapter(NodeConfig)


def step_config(plan: Plan, key: str, registry: Registry) -> BaseNodeConfig:
    """The config of step ``key``: registered, an existing node, or a requested stand-in."""
    s = plan.steps[key]
    if node := registry.get(s.node):
        return node.config
    if s.node in plan.requests and s.node not in NODES:
        return plan.requests[s.node].stand_in()(**s.config)
    return ADAPTER.validate_python({"name": s.node, **s.config})


def plan_prompt(hyp: Hypothesis) -> str:
    """What the builder reasons from: goal, inputs, criteria, and every attempt so far."""
    parts = [f"Goal: {hyp.goal}", f"Inputs: {json.dumps(hyp.describe_inputs())}"]
    parts.append(
        "Criteria (each needs an assertion): "
        + json.dumps({c.id: c.claim for c in hyp.criteria})
    )
    if hyp.hypothesis:
        parts.append(f"The user's own idea, to take or leave: {hyp.hypothesis}")
    if hyp.attempts:
        parts.append(
            "Attempts so far:\n"
            + json.dumps([a.summary() for a in hyp.attempts], indent=1)
        )
    return "\n\n".join(parts)


# The Amass records an agent was shown, by amassId, each with the core it is in.
Seen = dict[str, tuple[str, dict[str, Any]]]


def cite(plan: Plan, seen: Seen) -> list[Observation]:
    """The plan's observations, each filled in from the record it cites in ``seen``."""
    return [Observation.from_record(o, *seen[o.amass_id]) for o in plan.observations]


def build_agent(
    model: Model | str, registry: Registry, seen: Seen | None = None
) -> Agent[Hypothesis, Plan]:
    """Return an agent that writes a Plan for a goal, with each guard a retry.

    The agent makes the nodes it needs in ``registry`` with create_node, and can reuse
    the ones already there. Run it with ``deps=`` the Hypothesis, whose ``criteria`` and
    ``attempts`` the guards read. A plan may name a node that does not exist, if it
    carries a ToolRequest for it; the plan is typechecked as if the node were written.

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

    def check_plan(ctx: RunContext[Hypothesis], plan: Plan) -> Plan:
        hyp, reqs = ctx.deps, plan.requests
        if plan.inputs != hyp.input_kinds():
            raise ModelRetry(
                f"inputs must be exactly the goal's inputs: {hyp.input_kinds()}"
            )
        if unseen := sorted({o.amass_id for o in plan.observations} - seen.keys()):
            raise ModelRetry(
                f"Observations cite records you were not shown: {unseen}. Cite only "
                f"amassIds from search_literature or get_record: {sorted(seen)}"
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
            plan.typecheck(configs)
        except (ValidationError, ValueError, TypeError) as e:
            raise ModelRetry(str(e)) from e
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
            if isinstance(cfg, BaseFilterConfig) == (a.branch == "produced"):
                raise ModelRetry(
                    f"Assertion step {a.step!r}: use branch yes or no on a filter step, "
                    "and produced on any other step."
                )
        sides = {(a.step, a.branch) for a in plan.assertions}
        if both := sorted(s for s, b in sides if b == "yes" and (s, "no") in sides):
            raise ModelRetry(
                f"Assertions on both branches of {both} cannot both hold: a filter that "
                "takes every entity down one branch takes none down the other."
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
        instructions=BUILD_INSTRUCTIONS,
        tools=[
            Tool(list_nodes),
            Tool(search_nodes),
            Tool(describe_node),
            Tool(list_registry),
            Tool(create_node),
            Tool(search_literature),
            Tool(get_record),
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


INPUTS_INSTRUCTIONS = """\
Find the sequences a goal is about, so that a DAG can be planned on them.
Where the goal names a gene, an organism or an accession rather than giving a sequence,
find the record with search_sequences, fetch its coding sequences with fetch_sequences, and
pass the handles to add_input. Only pass `sequences` to add_input for a sequence written out
in the goal or the proposed hypothesis, copied exactly: never invent one, and never fill a
gap with a plausible-looking sequence. Add every input the goal needs, name each for what it
holds, and say where it came from in `source`. Stop when they are all added."""


class FoundInputs(BaseModel):
    """What the inputs agent added: the inputs, and where each came from."""

    inputs: dict[str, list[Value]] = {}
    sources: dict[str, str] = {}


def inputs_agent(model: Model | str) -> tuple[Agent[None, str], FoundInputs]:
    """Return an agent that finds a goal's input sequences in NCBI, and what it adds."""
    found = FoundInputs()
    # Every CDS the agent fetched, by handle, so that add_input can cite a record
    # instead of the agent retyping a sequence it was shown.
    fetched: dict[str, tuple[Value, str]] = {}
    value_adapter: TypeAdapter[Value] = TypeAdapter(Value)

    def search_sequences(
        term: str, limit: int = 5
    ) -> list[dict[str, Any]] | dict[str, str]:
        """Search NCBI Nucleotide for records, to find an accession to fetch a gene from.

        Args:
            term: An Entrez query, e.g. ``lacZ[gene] AND "Escherichia coli"[orgn]``.
            limit: How many records to return, at most.
        """
        try:
            return entrez.search(term, limit)
        except entrez.EntrezError as e:
            return {"error": str(e)}

    def fetch_sequences(
        accession: str, gene: str | None = None, limit: int = 5
    ) -> dict[str, Any]:
        """Fetch the coding sequences of an NCBI Nucleotide record, by accession.

        Each usable CDS comes back with a ``handle``. Give those handles to add_input:
        that way the input holds the sequence as NCBI has it, with no chance of a
        typo. A CDS that is not usable as coding DNA, e.g. a partial one or one with
        ambiguity codes, is reported with its problems and has no handle.

        Args:
            accession: The record's accession, e.g. ``NC_000913.3`` or ``J01636.1``.
            gene: Keep only the CDS of this gene. Name it when the record is a genome:
                it has thousands.
            limit: How many CDS to return, at most.
        """
        try:
            records = entrez.fetch_cds(accession, gene)
        except entrez.EntrezError as e:
            return {"error": str(e)}
        if not records:
            return {
                "error": f"{accession} has no CDS for gene {gene!r}. Call it again "
                "without `gene` to see what the record annotates."
            }
        out = []
        for i, r in enumerate(records[:limit]):
            item = {k: r[k] for k in ("gene", "protein", "location", "length")}
            if not r["usable"]:
                out.append({**item, "usable": False, "problems": r["problems"]})
                continue
            handle = f"{accession}:{r['gene'] or i}"
            where = f"NCBI {accession} CDS {r['gene'] or r['location']}"
            fetched[handle] = (Dna(sequence=r["sequence"]), where)
            # A whole CDS is long, and the handle is what add_input needs, so only
            # enough of the sequence to recognise it is echoed back.
            out.append(
                {
                    **item,
                    "usable": True,
                    "handle": handle,
                    "sequence": r["sequence"]
                    if r["length"] <= 1200
                    else f"{r['sequence'][:600]}…{r['sequence'][-60:]} (truncated; "
                    "add_input uses the full sequence)",
                }
            )
        return {"accession": accession, "cds": out, "total": len(records)}

    def add_input(
        name: str,
        source: str,
        handles: list[str] | None = None,
        sequences: list[str] | None = None,
        kind: str = "dna",
    ) -> dict[str, Any]:
        """Give the DAG an input: a named, non-empty list of entities of one kind.

        Use ``handles`` from fetch_sequences wherever you can. Use ``sequences`` only
        for a sequence the goal or the proposed hypothesis gives you verbatim.

        Args:
            name: What the DAG calls this input, e.g. ``seq``. No dots.
            source: Where these entities came from, in a phrase, e.g.
                "NCBI NC_000913.3 CDS thrA" or "given in the goal". This is recorded.
            handles: Handles from fetch_sequences.
            sequences: Literal sequences, upper case.
            kind: The kind of every entity given as ``sequences``. Handles are dna.
        """
        if "." in name or not name.strip():
            raise ModelRetry(
                f"Input name {name!r} must be a non-empty name with no dots."
            )
        if kind not in TYPES:
            raise ModelRetry(f"Unknown kind {kind!r}. Known kinds: {sorted(TYPES)}.")
        if unknown := sorted(set(handles or ()) - fetched.keys()):
            raise ModelRetry(
                f"No such handle: {unknown}. Handles come from fetch_sequences: "
                f"{sorted(fetched)}"
            )
        if handles and sequences and kind != "dna":
            raise ModelRetry(
                f"Handles are dna, so {name!r} cannot also hold {kind} sequences. "
                "Use two inputs, or give the sequences on their own."
            )
        items: list[Value] = [fetched[h][0] for h in handles or ()]
        try:
            items += [
                value_adapter.validate_python(
                    {"kind": kind, "sequence": s.strip().upper()}
                )
                for s in sequences or ()
            ]
        except ValidationError as e:
            raise ModelRetry(f"Input {name!r} is not valid {kind}: {e}") from e
        if not items:
            raise ModelRetry(f"Input {name!r} needs at least one handle or sequence.")
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
        tools=[
            Tool(search_sequences),
            Tool(fetch_sequences),
            Tool(add_input, max_retries=3),
        ],
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
