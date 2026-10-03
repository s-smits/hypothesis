import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator
from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.models import Model

from node_dag import amass, entrez
from node_dag.dag import Dag, DagOutput
from node_dag.factory import MAPPING, NodeConfig
from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.registry import Registry
from node_dag.types import TYPES, Dna, Entity, Value

NODES = {c.model_fields["name"].default: c for c in MAPPING}


class CriterionJudgement(BaseModel):
    """The critic's call on one success criterion.

    Args:
        criterion: Which of the hypothesis's criteria, by index into ``criteria``.
        met: Whether the outcome meets it, or None when the run gives no way
            to tell.
        reason: The evidence for the call.
    """

    criterion: int = Field(ge=0)
    met: bool | None
    reason: str


class Verdict(BaseModel):
    """The verifier's answer to "Did this workflow complete its goal?".

    Args:
        achieved: True only when the outcome does what the goal asks, for every input.
        reason: The expected result, the actual result, and how they compare.
        criteria: One judgement per criterion the hypothesis lists, in order.
    """

    achieved: bool
    reason: str
    criteria: list[CriterionJudgement] = []


class Criterion(BaseModel):
    """One success criterion a goal's outcome is judged against.

    Args:
        kind: ``quantitative`` for a criterion that names a measure or a
            comparison, ``qualitative`` for one that states a property to judge.
        text: What must hold for the goal to count as met, in a sentence.
    """

    kind: Literal["qualitative", "quantitative"]
    text: str = Field(min_length=1)


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


class Hypothesis(BaseModel):
    """A goal, a plan to meet it with a DAG, and what happened when the plan ran.

    Set ``goal``. ``run_hypothesis`` fills in the rest, in order: ``inputs``,
    ``hypothesis`` and ``dag`` (from the builder agent), ``workflow_id`` and
    ``outcome`` (the DagWorkflow run) and ``verdict`` (from the verifier agent).

    Args:
        id: Names the saved file. Generated if not given.
        goal: What the DAG must do, in plain English, e.g. "lower the atom count of the sequences".
        criteria: The success criteria of ``goal``: what must hold of the outcome
            for it to count as met. Written by the user or drafted by the criteria
            agent and then edited; empty means the goal speaks for itself.
        inputs: The list of entities to run on, keyed by DAG input name. Each list is
            not empty and holds one kind. Empty when the hypothesis starts: the
            builder agent chooses the inputs from the goal unless the caller gave
            them, and they are set by the time ``dag`` is.
        hypothesis: How the builder agent will build and run a DAG to meet ``goal``.
        observations: What the builder agent found in the literature that bears on
            ``hypothesis``.
        dag: The DAG the builder agent made.
        workflow_id: The ID of the DagWorkflow run that ran ``dag``.
        outcome: The result of running ``dag`` on ``inputs``.
        input_sources: Where each input came from, in a phrase, e.g. the NCBI record
            a CDS was fetched from. Provenance for inputs the builder chose itself.
        verdict: Whether ``outcome`` meets ``goal``.
        error: Why it stopped before it had a verdict, if it did.
        interrupted: True if the process running it stopped before it finished, so
            no one is working on it any more.
    """

    id: str = Field(default_factory=lambda: f"hypothesis-{uuid.uuid4()}")
    goal: str
    criteria: list[Criterion] = []
    inputs: dict[str, list[Value]] = {}
    input_sources: dict[str, str] = {}
    hypothesis: str | None = None
    observations: list[Observation] = []
    dag: Dag | None = None
    workflow_id: str | None = None
    outcome: DagOutput | None = None
    verdict: Verdict | None = None
    error: str | None = None
    interrupted: bool = False

    @field_validator("inputs")
    @classmethod
    def _check_inputs(cls, v: dict[str, list[Value]]) -> dict[str, list[Value]]:
        for name, items in v.items():
            if not items or len({i.kind for i in items}) != 1:
                raise ValueError(
                    f"Input {name!r} must be a list of one kind, not empty"
                )
        return v

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


class DraftStep(BaseModel):
    """One step of a DAG draft.

    Args:
        node: The id of a registered node, ``<node name>__<config hash>``, from
            create_node or list_registry. Steps may share a node.
        inputs: Maps the node's input port to a source: a DAG input name, a tool or
            scoring step key, or ``<filter step>.yes`` / ``<filter step>.no``.
    """

    node: str
    inputs: dict[str, str]


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
Build a DAG of nodes that meets the user's goal, and choose the sequences it runs on.
You make the nodes one at a time, in a registry, then wire them together. A config field
such as a reference sequence or a threshold must be a real value: copy or work it out
from the inputs, never a placeholder.

Inputs come first. The prompt shows the inputs you were given, which may be none at all.
- If it shows inputs, use them as they are. You cannot change or replace them.
- Otherwise work out from the goal what the DAG should run on, and declare it with
  add_input. Where the goal names a gene, an organism or an accession rather than
  giving you a sequence, find the record with search_sequences and fetch its CDS with
  fetch_sequences, then pass the handles to add_input. Only pass `sequences` to
  add_input for a sequence written out in the goal or the proposed hypothesis.
  Never write out a sequence from memory: a gene you half-remember is not that gene,
  and a made-up sequence makes the whole run meaningless. If you cannot find a real
  sequence for the goal, say so in your hypothesis rather than inventing one.
- Name an input for what it holds, e.g. `seq`. A step reads it by that name.

The prompt may also list the goal's success criteria, which the user signed off
on: the outcome will be judged against them, so build with them in mind.

Match the DAG to the goal's archetype:
- Measurement Archetype: Goal asks to measure, score, or convert given sequences
  (e.g. "Score sequence via ostir expression", "Convert DNA to protein").
  Topology: Input -> [Scorer | Converter].
- Screening Archetype: Goal asks to filter existing sequences against a threshold.
  Topology: Input -> Scorer -> Filter(threshold).
- Optimization / Search Archetype: Goal asks to find, produce, reduce, increase, or
  improve a metric (e.g. "find sequences with higher ostir expression").
  Topology: Input -> Generator (e.g. mutate_synonymous with variants_per_sequence > 1) ->
            Scorer -> Filter(threshold beating input baseline).
  RULES FOR OPTIMIZATION:
  1. You MUST generate candidate variants using a generation node (mutate_synonymous).
     Scoring and filtering alone cannot find what the inputs do not already hold!
  2. Increase variants_per_sequence (e.g. 10) on mutate_synonymous so a candidate pool
     is created for selection.
  3. Work out the input's baseline score, and set the filter threshold strictly relative
     to that baseline (e.g. threshold > baseline for higher expression). A threshold that every entity passes is not a filter, and a
     DAG that keeps everything has chosen nothing.

Steps:
1. Call search_nodes (with query and input_type) or list_nodes to find nodes by intent.
   Call list_registry for nodes already made. Reuse a registered node when it does what
   you need.
   When a choice depends on biology you are unsure of, such as a threshold or which
   measure fits the goal, call search_literature, and get_record for more of a hit.
2. Call describe_node for each kind you will make. Use only the fields its schema declares.
3. Call create_node for each node you need, with a short description of what it is for.
   Leave config_hash out: it is set for you. The reply shows the node's id, its input
   port and kind, its outputs, and, for a scorer, the full name of every score column it
   adds. Make a scorer before the filter that reads its column, and copy the column name
   from the scorer's reply into the filter's `column`.
4. Call submit_dag with your hypothesis: how the DAG meets the goal. Its `inputs` are
   exactly the inputs you were given plus the ones you added, name to kind. Each step names a
   registered node by id. Connect its input port to a source whose kind is the kind of
   that port. If you searched the literature, add an observation for each record that
   bears on the hypothesis: its amassId and a summary of what it found and how that
   shaped the DAG.

Every source is a list of entities, and a node runs once on the whole list that reaches it.
- A tool step makes new entities, under its key. They have no scores.
- A scoring step passes its entities on under its key, and adds its score columns.
- A filter step splits its entities into <step>.yes and <step>.no by its `column`.
Known kinds: {sorted(TYPES)}."""

VERIFY_INSTRUCTIONS = """\
You get a hypothesis as JSON: a goal, its inputs, the builder's plan to meet the goal
(hypothesis), the DAG that ran, and the outcome. The outcome holds every value the DAG
produced, keyed by step. Answer: did this workflow
complete its goal? Work out the expected result from the goal and the inputs yourself.
Do not trust the hypothesis or the DAG to be correct. Then compare it with the outcome. Set achieved to
true only if the outcome holds the expected result for every input.
Set achieved to false, whatever else the DAG did, when:
- The goal names a measure, a method or a node that the DAG did not use. A different
  node is not a substitute, and the hypothesis calling it one does not make it one.
- The goal asks to find or choose something, and the DAG chose nothing: it returns its
  inputs unchanged, or every entity passed its filter, or none did.
- The goal asks for higher, lower, more, less, or optimized values, but the outcome only
  holds the original input sequence(s) without improvement or with a filter threshold
  that trivialized selection (e.g. threshold 0.0 on positive scores).
- A threshold let everything through, so the filter decided nothing.
The hypothesis may list success criteria that qualify the goal. Judge the outcome
against each one and put one judgement per criterion in `criteria`, in the same
order: `criterion` is its index into the list, `met` is true, false or null when
the run gives no way to tell, and `reason` is the evidence. achieved still answers
the whole question: when the goal needs a criterion that did not hold, achieved is
false."""

CRITERIA_INSTRUCTIONS = """\
You get a goal for a sequence experiment, and sometimes a proposed hypothesis.
Draft its success criteria: the list of things that must hold of the DAG's
outcome for the goal to count as met. The user reviews and edits your list, so
make it a draft worth correcting, not a formality.

- Keep it short: two to six criteria, each one testable statement.
- Mark a criterion quantitative when it names a measure or a comparison, e.g.
  "expression above the input sequences'". Mark it qualitative when it states a
  property to judge, e.g. "the protein is unchanged".
- Cover what the goal leaves out: the hard constraints it implies (e.g. same
  protein, same length) as well as the improvement it asks for.
- Where the goal is vague about success, commit to a reasonable reading and say
  what it assumes in the criterion, so the user can correct it.
- Do not restate the goal in different words; each criterion must add a check."""


def build_agent(
    model: Model | str, registry: Registry
) -> Agent[Hypothesis, Hypothesis]:
    """Return an agent that writes a hypothesis and a validated Dag for a goal.

    The agent makes the nodes it needs in ``registry``, one by one with create_node,
    and can reuse the ones already there. Its DAG steps name registered nodes.

    Run it with ``deps=`` the Hypothesis. It returns a copy with ``hypothesis`` and
    ``dag`` set. The DAG must declare exactly the Hypothesis's inputs.
    """
    adapter: TypeAdapter[NodeConfig] = TypeAdapter(NodeConfig)
    value_adapter: TypeAdapter[Value] = TypeAdapter(Value)

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
            node, new = registry.register(adapter.validate_python(config), description)
        except (ValidationError, ValueError) as e:
            raise ModelRetry(str(e)) from e
        return {**node.summary(), "new": new}

    def list_registry() -> list[dict[str, Any]]:
        """List every registered node: id, description, config, input, outputs, score columns."""
        return [n.summary() for n in registry.all()]

    # Every Amass record the agent was shown, by amassId, with its core. An
    # observation must cite one of these, so it cannot cite a record it made up.
    seen: dict[str, tuple[str, dict[str, Any]]] = {}

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

    # The inputs the agent chose with add_input, and where each came from. The
    # caller's own inputs, when it gave any, are not in here and cannot be replaced.
    drafted: dict[str, list[Value]] = {}
    sources: dict[str, str] = {}
    # Every CDS the agent fetched, by handle, so that add_input can cite a record
    # instead of the agent retyping a sequence it was shown.
    fetched: dict[str, tuple[Value, str]] = {}

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
        ctx: RunContext[Hypothesis],
        name: str,
        source: str,
        handles: list[str] | None = None,
        sequences: list[str] | None = None,
        kind: str = "dna",
    ) -> dict[str, Any]:
        """Give the DAG an input: a named, non-empty list of entities of one kind.

        Use ``handles`` from fetch_sequences wherever you can. Use ``sequences`` only
        for a sequence the goal or the proposed hypothesis gives you verbatim: copy
        it, never invent one, and never fill a gap with a plausible-looking sequence.

        Args:
            name: What the DAG calls this input, e.g. ``seq``. No dots.
            source: Where these entities came from, in a phrase, e.g.
                "NCBI NC_000913.3 CDS thrA" or "given in the goal". This is recorded.
            handles: Handles from fetch_sequences.
            sequences: Literal sequences, upper case.
            kind: The kind of every entity here. Ignored for handles, which are dna.
        """
        if "." in name or not name.strip():
            raise ModelRetry(
                f"Input name {name!r} must be a non-empty name with no dots."
            )
        if name in ctx.deps.inputs:
            raise ModelRetry(
                f"Input {name!r} was given with the goal and cannot be replaced."
            )
        if kind not in TYPES:
            raise ModelRetry(f"Unknown kind {kind!r}. Known kinds: {sorted(TYPES)}.")
        if unknown := sorted(set(handles or ()) - fetched.keys()):
            raise ModelRetry(
                f"No such handle: {unknown}. Handles come from fetch_sequences: "
                f"{sorted(fetched)}"
            )
        items: list[Value] = [fetched[h][0] for h in handles or ()]
        if handles and sequences and kind != "dna":
            raise ModelRetry(
                f"Handles are dna, so {name!r} cannot also hold {kind} sequences. "
                "Use two inputs, or give the sequences on their own."
            )
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
        drafted[name] = items
        sources[name] = source
        return {
            "name": name,
            "kind": items[0].kind,
            "count": len(items),
            "lengths": [len(i.sequence) for i in items],
            "source": source,
            "inputs_so_far": sorted({**drafted, **ctx.deps.inputs}),
        }

    def submit_dag(
        ctx: RunContext[Hypothesis],
        hypothesis: str,
        inputs: dict[str, str],
        steps: dict[str, DraftStep],
        observations: list[DraftObservation] | None = None,
    ) -> Hypothesis:
        """Submit your hypothesis and the DAG. If the DAG is not valid, you get the error.

        Args:
            hypothesis: How this DAG meets the goal: what each step does and why.
            inputs: Maps each DAG input name to the kind of its entities. Must be
                exactly the inputs of the hypothesis: the ones given with the goal
                and the ones you made with add_input.
            steps: The steps, keyed by name. A key must not contain ``.``.
            observations: The findings from search_literature or get_record that bear
                on the hypothesis, one per record. Leave out if you did not search.
        """
        # The caller's own inputs win: add_input refuses to shadow one.
        available: dict[str, list[Value]] = {**drafted, **ctx.deps.inputs}
        if not available:
            raise ModelRetry(
                "This hypothesis has no inputs yet. Work out from the goal what the "
                "DAG should run on, find it with search_sequences and fetch_sequences, "
                "and declare it with add_input before submitting."
            )
        kinds = {k: v[0].kind for k, v in available.items()}
        if inputs != kinds:
            raise ModelRetry(f"inputs must be exactly the hypothesis's inputs: {kinds}")
        observations = observations or []
        if unseen := sorted({o.amass_id for o in observations} - seen.keys()):
            raise ModelRetry(
                f"Observations cite records you were not shown: {unseen}. Cite only "
                f"amassIds from search_literature or get_record: {sorted(seen)}"
            )
        cited = [Observation.from_record(o, *seen[o.amass_id]) for o in observations]
        nodes = {n.id: n for n in registry.all()}
        if unknown := sorted({s.node for s in steps.values()} - nodes.keys()):
            raise ModelRetry(
                f"Not registered: {unknown}. Make them with create_node. "
                f"Registered: {sorted(nodes)}"
            )

        # Semantic check: optimization goals with 1 input sequence must generate candidates
        comparative_words = (
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
        goal_lower = ctx.deps.goal.lower()
        is_comparative = any(w in goal_lower for w in comparative_words)
        total_input_count = sum(len(v) for v in available.values())

        if is_comparative and total_input_count == 1:
            has_generation = any(
                "generation" in [c.value for c in nodes[s.node].config.categories]
                for s in steps.values()
            )
            if not has_generation:
                raise ModelRetry(
                    f"The goal asks to optimize or find improved sequences ({ctx.deps.goal!r}), "
                    "but only 1 input sequence was provided and the DAG has no generation node "
                    "(e.g. mutate_synonymous) to create candidate variants. A DAG that only "
                    "measures the input cannot find sequences with higher/lower metrics. "
                    "Add a generation step (e.g. mutate_synonymous with variants_per_sequence > 1) "
                    "to generate variants, score them, and filter for those that beat the baseline."
                )

        # Semantic check: reject trivial filter thresholds like 0.0 on positive scores
        for step_name, s in steps.items():
            cfg = nodes[s.node].config
            if isinstance(cfg, BaseFilterConfig):
                if (
                    cfg.name == "at_least"
                    and "expression" in cfg.column
                    and cfg.threshold <= 0.0
                ):
                    raise ModelRetry(
                        f"Step {step_name!r} filters on expression with threshold {cfg.threshold}, "
                        "which allows every sequence to pass trivially. Determine the baseline score "
                        "and set a threshold that requires candidates to beat the baseline."
                    )

        try:
            dag = Dag.model_validate(
                {
                    "inputs": inputs,
                    "steps": {
                        k: {
                            "config": nodes[s.node].config.model_dump(mode="json"),
                            "inputs": s.inputs,
                        }
                        for k, s in steps.items()
                    },
                }
            )
        except ValidationError as e:
            raise ModelRetry(str(e)) from e
        return ctx.deps.model_copy(
            update={
                "inputs": available,
                "input_sources": {**sources, **ctx.deps.input_sources},
                "hypothesis": hypothesis,
                "observations": cited,
                "dag": dag,
            }
        )

    return Agent(
        model,
        deps_type=Hypothesis,
        instructions=BUILD_INSTRUCTIONS,
        tools=[
            Tool(list_nodes),
            Tool(search_nodes),
            Tool(describe_node),
            Tool(list_registry),
            Tool(create_node),
            Tool(search_sequences),
            Tool(fetch_sequences),
            # Getting an input right can take a few goes: a handle that does not
            # exist, then a sequence that is not valid, is an ordinary sequence of
            # mistakes to correct rather than a reason to give up on the run.
            Tool(add_input, max_retries=3),
            Tool(search_literature),
            Tool(get_record),
        ],
        output_type=submit_dag,
        retries={"output": 3},
    )


def criteria_agent(model: Model | str) -> Agent[None, list[Criterion]]:
    """Return an agent that drafts a goal's success criteria for the user to edit."""
    return Agent(model, instructions=CRITERIA_INSTRUCTIONS, output_type=list[Criterion])


def verify_agent(model: Model | str) -> Agent[None, Verdict]:
    """Return an agent that judges whether a run Hypothesis met its goal."""
    return Agent(model, instructions=VERIFY_INSTRUCTIONS, output_type=Verdict)
