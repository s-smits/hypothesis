# Review: one five-round loop run, and what its acceptance rule is doing

Read the run in `runs/d10361e4-verbose.zip` and review how the hypothesis loop decided each of its rounds, and whether the acceptance rule and the stage prompts are doing what they are for. This document is the task, not a draft to critique: do not suggest changes to its wording or framing, and do not ask which mode to work in.

## The question and the decision it changes

In round 3 the verifier judged this run's outcome right and the code still rejected the round on `held`; by round 5 the run was blocked on a node nobody has written, with 445,053 of its 500,000 tokens spent. Is that the acceptance rule working as intended, or are the criteria stage, the builder prompt and the node library pushing the builder away from what the goal needs? The answer decides which of three things changes before the next batch of runs: the prompts, the acceptance rule (`accepted()` and `holds()`), or the node library (a node that checks protein identity). It also decides whether the 500,000-token budget should rise.

## What you can read

The repository is private and you cannot open it. This document quotes the source its argument rests on, each block with its `Path:` line, and the zip carries the whole files under `source/` at the commit the run used. `source/AGENTS.md` is the guide to how the system works. The inputs are the three DNA sequences in `source/expression_goal.json`; no held-out benchmark data is in the bundle.

| commit | what it is | published |
| --- | --- | --- |
| `be3805f` | the code every model call of this run executed: the worker's checkout, with no tracked change since it started | no, local only. `patches/loop-vs-published.diff` is its difference from the published tip of PR #2 |
| `ccbe2a7` | the loop's "three fixes" commit, under `ccf6453` | yes |
| `ccf6453` | published tip of PR #2 (branch `hypothesis-loop-compact`), the loop | yes |
| `cea9f15` | the main commit `be3805f` merged: "Add AMASS literature tools, observations UI and ESMFold2 node", which also removed the atom-count node `dna_atom_score` | yes |

- `runs/`: one zip per saved run, each with its own `README.md` and `MANIFEST.json` that say what it holds and how to read it. `d10361e4` is the run under review, at verbose depth. `9afcd59e` is the run before it on the same goal, at medium depth. The other saved runs are at light depth, so every run's decisions are present.
- `source/`: whole files at `be3805f`, the node configs the builder reads, the goal this run used (`expression_goal.json`) and the earlier goal (`examples/optimise.json`).
- `patches/`: the unpublished commits. `pr/pr2.json`: the title, body and commits of PR #2, which a private repository does not serve. The PR body carries the operator's own earlier reading of the atom-count runs: treat it as a claim to test, not as evidence.

## What happened

**The run.** Goal: "Raise the translation initiation rate of the DNA sequences without changing their proteins. Keep the ones that score higher than the first sequence." on three DNA sequences. Every stage (criteria, plan, verify, critique) ran on `claude-sonnet-5-5`. The round limit was 20; the token budget is the default 500,000, which the command line cannot change. Code: `be3805f`. Results: `d10361e4-verbose/hypotheses/hypothesis-d10361e4-df7c-4044-a8fc-8949934a6c0a.json` holds the Hypothesis; `d10361e4-verbose/trajectories/` holds every call.

**Criteria, fixed before any plan** (call: `trajectories/hypothesis-d10361e4-df7c-4044-a8fc-8949934a6c0a-r0-criteria.json`):

- `protein_preserved`: Each output DNA sequence translates to exactly the same protein (amino acid sequence) as its corresponding input sequence, with no changes to the coding content or length.
- `initiation_rate_increased`: Each output sequence has a predicted translation initiation rate that is higher than that of the first input sequence, when scored by the same translation initiation rate predictor.
- `only_higher_kept`: The output contains only sequences whose translation initiation score is strictly greater than the first sequence's score; any sequence scoring equal or lower is excluded.
- `valid_dna_output`: Every output sequence is a valid DNA sequence consisting only of the characters A, C, G and T.

**Rounds** (`step:node` per plan; `held` is worked out by code, the branch took every entity and the other took none):

| round | plan steps | assertions sit on | held | entities each branch took | verifier | critic root cause | plan call |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r1 | mutate:mutate_synonymous, score:ostir_expression, keep:at_least | keep.yes | keep.yes=False | keep.yes=37, keep.no=3 | agrees=false covers_goal=false | wrong_config | 4 calls, 34,442 tokens |
| r2 | - | - | - | - | error | goal_misread | 8 calls, 119,485 tokens |
| r3 | mutate:mutate_synonymous, score:ostir_expression, keep:at_least | keep.yes | keep.yes=False | keep.yes=5, keep.no=35 | agrees=true covers_goal=true | wrong_config | 3 calls, 34,128 tokens |
| r4 | mutate:mutate_synonymous, score:ostir_expression, keep:at_least, rescore:ostir_expression, keep_confirm:at_least | keep_confirm.yes | keep_confirm.yes=True | keep_confirm.yes=5, keep_confirm.no=0 | agrees=true covers_goal=false | wrong_wiring | 4 calls, 58,571 tokens |
| r5 | mutate:mutate_synonymous, score:ostir_expression, keep:at_least, check:protein_identity_check, same_protein:at_most, valid_dna:at_most | keep.no, keep.yes, same_protein.yes, valid_dna.yes | - | - | not run | - | 6 calls, 119,140 tokens |

Round 5's plan was accepted by the guards and asked for `protein_identity_check`, a `score` node over `dna` sequences, with outputs `amino_acid_changes, non_acgt_count`. The run has been blocked on it since, and the state is `blocked`. The request's own reason for needing it: "No existing node checks protein equality or the DNA alphabet, so protein_preserved and valid_dna_output cannot otherwise be proven by a filter branch."

**A property of round 5's plan, derived from `holds()` below.** Its assertions on the `keep` filter are `keep.yes` (initiation_rate_increased) and `keep.no` (only_higher_kept). `holds()` requires an assertion's branch to take at least one entity and the *other* branch to take none, so those two cannot both hold in any one outcome. The plan passed the plan guards, which do not evaluate `holds()`, and has not run, because the node it asks for does not exist.

**What the models said, quoted from the saved files:**

**r1 verifier** (`agrees=false`, `covers_goal=false`; the line beginning `Acceptance:` is added by code):

> The filter does not compare against the first sequence's score. It uses a fixed threshold of 1.0, and the first sequence (ATGGCTCTGAAATAA, id 1e142be77490) scores 8.746. The expected result keeps only variants scoring above 8.746. The kept set has 37 of 40 items, including the first sequence itself and many scoring 2-4, which are below its baseline. Only 3 were excluded, so the threshold barely filtered anything. The goal requires strictly higher than the first sequence, so this fails. The plan's assertions are also unsound: the plan admits the threshold is an estimate, `held` is false, and none of the assertions checks the baseline comparison or that the protein is preserved. The terminal codons also change (TAA to TAG or TGA), which are synonymous stops, but the plan never verifies this.
> 
> Acceptance: these assertions did not hold: ['keep.yes']

**r1 critic** (`wrong_config`): The keep step used at_least with a hard-coded threshold of 1.0, a guess made without running OSTIR. The first sequence (id 1e142be77490) actually scores 8.746, so the filter did not compare against it. 37 of 40 variants passed, including the baseline itself and many scoring 2-4. mutate and score ran correctly. Only the filter's threshold was wrong, so the keep.yes set violates 'strictly greater than the first sequence'.

**r2** produced no plan. The error recorded: `no valid plan: Exceeded maximum output retries (3)`. Its retries, from `trajectories/hypothesis-d10361e4-df7c-4044-a8fc-8949934a6c0a-r2-plan.json`:

  - `No assertion covers ['protein_preserved', 'valid_dna_output'].`
  - `Unknown node 'dna_atom_score'. Known nodes: ['at_least', 'at_most', 'codon_count', 'dna_complement', 'dna_reverse_complement', 'dna_to_protein', 'dna_transcribe', 'esmfold2_fold', 'mutate_synonymous', 'ostir_expression', 'recode_codons', 'rna_back_transcribe']`
  - `[{'type': 'value_error', 'loc': ['requests', 'protein_change_count'], 'msg': "Value error, Unknown kind 'score'; known: ['amino_acid_sequence', 'dna', 'protein_structure', 'rna']", 'input': {'name': 'protein_change_count', 'node': 'score', 'purpose': "Count am`
  - `[{'type': 'value_error', 'loc': ['requests', 'protein_change_count'], 'msg': 'Value error, A scorer needs a list of score names as its output', 'input': {'name': 'protein_change_count', 'node': 'score', 'purpose': "Count amino-acid differences between each DNA`

**r3 verifier** (`agrees=true`, `covers_goal=true`; the line beginning `Acceptance:` is added by code):

> All three inputs translate to M-A-L-K-stop, and each of the five kept variants does too (e.g. ATGGCACTAAAATAA = M A L K *). The first input scores 8.746. The five kept variants score 9.86 or 10.68, so all are strictly higher and none sit in the narrow 8.746 to 8.75 gap. Variants at 8.746 or below, including the unchanged first sequence, went to keep.no. The filter therefore decided something, and every kept sequence is valid A/C/G/T DNA. The four assertions map onto the four criteria. The protein and DNA-validity assertions are weak, since the filter does not test them and they rest on how mutate works, but they still hold in the outcome.
> 
> Acceptance: these assertions did not hold: ['keep.yes']

**r3 critic** (`wrong_config`): The pipeline output satisfies the goal on its numbers, but the plan's keep step is wrong in construction. mutate and score ran correctly. keep is an inclusive at_least filter with a threshold of 8.75, a literal copied from the first input's score (8.746) as seen in an earlier run. It is not tied to the first sequence's score in this run. The criterion needs a strict comparison against that score. The 8.75 literal only approximates it, and an inclusive filter would pass a variant scoring exactly 8.75. The plan's own assertions on keep.yes (strictly higher, protein preserved) depend on this hand-picked constant, and nothing in the DAG checks them. keep.yes was therefore marked as not held, even though the five kept variants scoring 9.86 to 10.68 are in fact above 8.746.

**r4 verifier** (`agrees=true`, `covers_goal=false`; the line beginning `Acceptance:` is added by code):

> The outcome is correct. The mutate step produced synonymous variants of all three inputs, and each variant still encodes M-A-L-K-stop. OSTIR scored them, and the filter at 8.7461 sits just above the first sequence's 8.746, so it decided something: 5 of 39 passed. The five survivors score 9.86 or 10.68, and the unchanged parent, 8.746 variants and 8.4031 variant were excluded. All outputs are plain A/C/G/T. The assertions do not cover the goal, though. All four sit on keep_confirm.yes, a branch that only tests the score, and the rescore and confirm filter simply repeat the first filter on identical scores. The protein_preserved and valid_dna_output assertions rest on the claim that mutate_synonymous is synonymous by construction. Nothing in the branch partition checks the protein or the alphabet.
> 
> Acceptance: the verifier judged the assertions not to cover the goal

**r4 critic** (`wrong_wiring`): The numbers are right, but the plan is wired so its assertions cannot prove the goal. mutate, score and keep worked: 5 of 39 variants scored above 8.746 (9.86 or 10.68), and the parent and the 8.746 and 8.4031 variants were excluded. The added rescore and keep_confirm steps run the same OSTIR scorer and the same at_least filter on the same scores, so they test nothing new. All four assertions sit on keep_confirm.yes, a branch that partitions only on score. protein_preserved and valid_dna_output are therefore asserted from an assumption about mutate_synonymous, and no branch in the DAG partitions on protein equality or the A/C/G/T alphabet.

**Spend.** 445,053 tokens in total, which is the sum of the usage in every saved call: criteria 815, plan 365,766 (82%), verify 35,745, critique 42,727. The round 2 plan call (the one that ran out of output retries) took 119,485 tokens over 8 requests and the round 5 plan call 119,140 over 6. Guard retries: 7. The largest single request in the whole run was 23,944 input tokens. Compaction is set to trigger at 150,000 input tokens in one request and, in this run, did not.

**Every run in the ledger**, as recorded by the code (nothing in it is judged by a model). The three denominators are kept apart: achieved, not achieved, and no result.

| run | goal | state | rounds | tokens | ended (UTC) | models |
| --- | --- | --- | --- | --- | --- | --- |
| `f8529510` | Lower the atom count of the DNA sequen… | achieved | 3 | 221,358 | 17:42 | plan sonnet-5-5, verify haiku-4-5-20251001 |
| `f1bbba3e` | Lower the atom count of the DNA sequen… | achieved | 3 | 222,822 | 17:42 | plan sonnet-5-5, verify haiku-4-5-20251001 |
| `6b82eb8e` | Lower the atom count of the DNA sequen… | not achieved | 3 | 238,310 | 17:42 | plan sonnet-5-5, verify haiku-4-5-20251001 |
| `bbe2cc75` | Lower the atom count of the DNA sequen… | not achieved | 3 | 183,363 | 17:47 | plan sonnet-5-5, verify haiku-4-5-20251001 |
| `5bf5f3e1` | Lower the atom count of the DNA sequen… | not achieved | 3 | 201,149 | 18:26 | plan sonnet-5-5, verify haiku-4-5-20251001 |
| `9afcd59e` | Raise the translation initiation rate … | abandoned | 2 | 170,098 | 19:07 | plan sonnet-5-5, verify haiku-4-5-20251001 |
| `d10361e4` | Raise the translation initiation… | blocked (not yet in the ledger) | 5 | 445,053 | open | plan sonnet-5-5, verify sonnet-5-5 |

Achieved 2, not achieved 3, no result 2 (one abandoned, one blocked). The five earlier runs share the goal "Lower the atom count…", which used a node that has since been removed, with the default 3-round limit. Their saved files no longer load into the current code, and `runs/` still carries them, with the reason in each manifest. Two more saved runs with the goal "remove TCG" (`5544e680`, `89ffa657`) are in no ledger line, and how they were produced was not established. The ledger records no commit. For orientation only: commit `ccbe2a7` (the three fixes) and `ccf6453` are at 18:30 UTC and `be3805f` is at 18:45 UTC.

## The source these claims rest on

### Acceptance: what `held` means and what accepts a round

Path: `src/node_dag/plan.py:304-345`

```python
def holds(assertions: list[Assertion], outcome: DagOutput | None) -> dict[str, bool]:
    """Whether each assertion's branch took every entity and the other took none."""
    values = outcome.values if outcome else {}

    def n(source: str) -> int:
        return len(values[source].items) if source in values else 0

    return {
        f"{a.step}.{a.branch}": n(f"{a.step}.{a.branch}") > 0
        and n(f"{a.step}.{'no' if a.branch == 'yes' else 'yes'}") == 0
        for a in assertions
    }


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
```

### The instructions each stage is given

Path: `src/node_dag/agent.py:244-305`

```python
BUILD_INSTRUCTIONS = f"""\
Plan a DAG of nodes that meets the user's goal. You are shown the input sequences and the
criteria you will be marked against, which you cannot change. A config field such as a
reference sequence or a threshold must be a real value from those inputs, never a placeholder.

Shapes that usually fit a goal:
- Measure or convert given sequences: input -> scorer or converter.
- Screen sequences against a threshold: input -> scorer -> filter.
- Find, improve, raise or lower something: input -> generator (e.g. mutate_synonymous or
  recode_codons) -> scorer -> filter. Scoring and filtering alone cannot find what the
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
covers_goal to true only if the assertions genuinely test every criterion. You cannot
declare success: false is a veto and true grants nothing.
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
back to a wiring an earlier round already ran."""

CRITERIA_INSTRUCTIONS = """\
Turn the goal into one to four criteria that decide whether it was met. Each is a claim a
filter over the DAG's output could check. State what must be true, not how to do it."""
```

### What the verifier and critic are shown

Path: `temporal/hypothesis/activities.py:149-166`

```python
def _view(hyp: Hypothesis) -> dict[str, Any]:
    """The current round as a judge sees it. It has no earlier verdicts."""
    a = hyp.attempts[-1]
    assert a.plan
    return {
        "goal": hyp.goal,
        "criteria": hyp.criteria,
        "inputs": hyp.describe_inputs(),
        "hypothesis": a.plan.hypothesis,
        "expected": a.plan.expected,
        "assertions": a.plan.assertions,
        "held": a.held,
        "dag": a.dag,
        "outcome": a.outcome,
        "error": a.error,
    }


```

### The token check and the acceptance step in the round loop

Path: `temporal/hypothesis/loop.py:165-176`

```python
                )
            await self._set(criteria=out.criteria)
        for rnd in range(1, inp.max_rounds + 1):
            if self._abandoned:
                return await self._stop("abandoned", "a person abandoned the run")
            if self._hyp.usage.get("total", 0) > inp.max_tokens:
                return await self._stop(
                    "not achieved", f"spent more than {inp.max_tokens} tokens"
                )
            # Earlier rounds keep their previews and drop the raw outcome, to keep this small.
            old = [a.model_copy(update={"outcome": None}) for a in self._hyp.attempts]
            await self._set(attempts=old, round=rnd, state="building")
```

### Where a verdict is made from the verifier's opinion and the acceptance rule

Path: `temporal/hypothesis/loop.py:248-268`

```python
            if att.outcome is not None:
                judged = await self._ask("verify", verify_outcome, inp.verify_model)
                op = judged.opinion or VerifyOpinion(
                    agrees=False,
                    covers_goal=False,
                    reason=f"the verifier failed: {judged.error}",
                )
                ok, why = accepted(self._hyp.criteria, att.plan, op, att.outcome)
                verdict = Verdict(
                    achieved=ok,
                    reason=f"{op.reason}\n\nAcceptance: {why}",
                    agrees=op.agrees,
                    covers_goal=op.covers_goal,
                )
                att = await self._put(att, verdict=verdict)
                if ok:
                    return await self._stop("achieved", why)
            await self._set(state="critiquing")
            crit = await self._ask("critique", critique_attempt, inp.build_model)
            await self._put(att, critique=crit.critique)
        return await self._stop("not achieved", f"out of rounds after {inp.max_rounds}")
```

### The contract of a requested node (its fields carry no descriptions)

Path: `src/node_dag/plan.py:58-82`

```python
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
```

### Node descriptions that still speak of atom count (this one, then `mutate_synonymous`)

Path: `src/node_dag/nodes/filters/at_most/config.py:20-32`

```python
    inputs: ClassVar = {"items": Entity}
    intents: ClassVar = (
        "keep entities with score at most threshold",
        "filter for lower scores or values below a maximum cutoff",
        "select candidate sequences with reduced atom count",
        "filter to keep only synonymous sequences (amino_acid_changes at most 0)",
    )
    when_to_use: ClassVar = (
        "Use after a scoring node to select entities whose score is at or below a "
        "threshold (e.g. fewer atoms <= baseline - 1, or amino_acid_changes <= 0)."
    )
    when_not_to_use: ClassVar = (
        "Do not use when filtering for higher values (use at_least instead)."
```

Path: `src/node_dag/nodes/tools/mutate_synonymous/config.py:40-48`

```python
        "explore codon space for optimization or directed evolution",
    )
    when_to_use: ClassVar = (
        "Use whenever the goal asks to find, improve, optimize, increase, or lower a "
        "property (such as atom count or expression) while keeping the protein unchanged. "
        "Set variants_per_sequence > 1 (e.g. 10) to create a candidate pool to score and filter."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal is only to measure, score, or translate the given "
```

## What I doubt

Kept apart from the facts. Each premise may be rejected, and a doubt is listed only because its answer would change what happens next.

1. **`holds()` cannot be met by a selecting filter's own branch, so it rewards filters that decide nothing.**
   - *Believe:* an assertion on `keep.yes` holds only if `keep.no` took nothing, which is false for any filter that drops an entity. A builder can only pass by asserting on a second filter that repeats the first (round 4, where `keep_confirm.yes` held) or fails the round (rounds 1 and 3). Round 3's verifier agreed the outcome was right and the code still rejected it on `held`.
   - *Why:* `holds()` as quoted above, `held` in the round table, and round 5's two `keep` assertions, which no outcome can satisfy together.
   - *Refuted by:* a round in which a selecting filter dropped entities and its own branch's assertion held (the rule makes that impossible), or evidence that round 3's `held` came from something other than `keep.no` being non-empty.
   - *Look at:* `source/src/node_dag/plan.py`, `runs/d10361e4-verbose.zip` under `hypotheses/hypothesis-d10361e4-df7c-4044-a8fc-8949934a6c0a.json` attempts 3, 4 and 5, and `workflows/hypothesis-d10361e4-df7c-4044-a8fc-8949934a6c0a-r3.json` for what each branch took.
2. **The criteria stage asks for claims the node library cannot check.**
   - *Believe:* `protein_preserved` and `valid_dna_output` are true by construction of `mutate_synonymous`, and no registered filter tests them, so the loop can only accept by having a node written (round 5) or by hanging those assertions on an unrelated branch (rounds 3 and 4).
   - *Why:* the criteria instruction asks for "a claim a filter over the DAG's output could check", and `requests/protein_identity_check.json` says in `why_not_composable` that no existing node checks protein equality.
   - *Refuted by:* a plan over registered nodes only that covers all four criteria with assertions that hold and that a verifier accepts, or evidence that other goals' criteria were all checkable.
   - *Look at:* `trajectories/*-r0-criteria.json`, `requests/protein_identity_check.json`, `source/src/node_dag/agent.py` (`CRITERIA_INSTRUCTIONS`).
3. **The verifier's `covers_goal` is not stable for the same kind of assertion.**
   - *Believe:* in round 3 the verifier called the protein and DNA-validity assertions "weak ... but they still hold" and set `covers_goal=true`. In round 4 it vetoed assertions of the same kind with `covers_goal=false`. What differed is that round 4 also repeated a filter, so the veto may have been about the repeat and not about by-construction claims.
   - *Why:* the two quoted verdicts above.
   - *Refuted by:* the round 4 verify call showing it weighed the repeated filter and not the by-construction claim, or evidence the two rounds' assertions were not alike.
   - *Look at:* `trajectories/*-r3-verify.json`, `*-r4-verify.json`; `VERIFY_INSTRUCTIONS` in `source/src/node_dag/agent.py`.
4. **Spend is driven by plan calls and their retries, and the retry reasons point at the request schema.**
   - *Believe:* 82% of tokens are in plan calls. The retries in rounds 2 and 5 include `Unknown kind 'score'`, `Unknown kind 'scoring'` and `A scorer needs a list of score names as its output`: `ToolRequest.kind`, `port` and `output` carry no descriptions, so the builder invents values. Round 2 also called a node that no longer exists, `dna_atom_score`, and the node descriptions still talk of atom count.
   - *Why:* the per-round plan cost in the table, the retries quoted above and `ToolRequest` as quoted.
   - *Refuted by:* retries whose causes are not the request schema, or a run with the schema described that still spent as much.
   - *Look at:* `trajectories/*-r2-plan.json` and `*-r5-plan.json`; `source/src/node_dag/plan.py`; the node configs under `source/src/node_dag/nodes/`.
5. **The counts across runs are not comparable.**
   - *Believe:* "2 achieved of 5" mixes a goal that has since lost its node, a 3-round limit, more than one code version and, in some runs, a different verifier model. It says little about the code that ran here.
   - *Why:* the ledger table, which has a models column, and the ledger recording no commit.
   - *Refuted by:* a rerun of one goal on one commit, or evidence the code and prompts did not change between those runs.
   - *Look at:* `ledger.jsonl` in the verbose zip, each run's manifest.
6. **Compaction has not been exercised, and may not be reachable at the default budget.**
   - *Believe:* compaction cuts a call's context past 150,000 tokens. This run spent 445,053 tokens over 32 model requests and its largest request carried 23,944 input tokens, so a call's context would have to grow about 6 times beyond the largest seen before compaction is reached, and the 500,000-token budget may stop a run first.
   - *Why:* the spend paragraph, the token check in `loop.py` quoted above, and `compaction()` in `source/temporal/hypothesis/activities.py`.
   - *Refuted by:* a run whose single request exceeds 150,000 input tokens, or a trajectory with a compaction block.
   - *Look at:* per-request `usage` in the plan trajectories; `compaction()` and `COMPACT_WINDOW`.

## Deliver this

1. Findings, ranked by how much each would raise the share of runs reaching `achieved` per token spent. For each: the owner (the criteria prompt, the builder prompt, `accepted()` or `holds()`, the verifier prompt, the critic prompt, a node, or the environment), the evidence path inside the zips, a falsifier and a confidence.
2. For each doubt above: accept, reject or refine, with the evidence.
3. One change to make first, and the check that would show it worked.
4. What the bundle could not establish, including anything that needs the Temporal event history, which is not here.
