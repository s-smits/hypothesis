"""One skill: the rules the builder reads before it plans, distilled from recorded runs.

A skill is a single markdown file of short rules in named sections. This module is the
distillation, after Trace2Skill (Ni et al., arXiv 2603.25158): an analyst reads one recorded
round and proposes a patch to the skill, patches are merged in batches until one remains,
and the merged patch is applied. Because every patch is made against one file, a lesson that
recurs in many rounds is merged into one rule instead of being stored once per round.

What it does not do. It reads no run: ``temporal.skills`` turns saved runs into ``Record``s,
each labelled by the caller. It does not decide that a skill helps: a patch that applies is
a proposal, and whether it raises the benchmark score is a separate comparison. And it keeps
none of Trace2Skill's benchmark machinery (references folders, a translation pass, an LLM
format check): the skill is one file, and an edit is checked by code or rejected.

Provenance is kept by code, never asked of a model. The analyst sees one record, so its edits
carry that record's id; a merge names the numbered edits each merged edit came from, and the
merged edit carries their ids. An edit no source supports is dropped. The ids sit in HTML
comments in the file, which ``body`` strips, so the builder reads rules without bookkeeping.
"""

import asyncio
import hashlib
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunUsage
from pydantic_ai.exceptions import ContentFilterError, UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

__all__ = [
    "BATCH",
    "MAX_LINES",
    "SEED",
    "Edit",
    "Evolution",
    "Op",
    "Patch",
    "Record",
    "analyst_agent",
    "apply",
    "body",
    "digest",
    "evolve",
    "merge_agent",
]

# What the builder reads, in lines of rules, so a skill cannot grow past what a plan call
# should carry. An edit that would pass it is rejected, and a later deletion frees room.
MAX_LINES = 120
# Patches per merge call, which is what Trace2Skill used.
BATCH = 5
WORKERS = 4

Label = Literal["pass", "fail"]
Op = Literal["add_section", "append_to_section", "replace_in_section", "delete_section"]

# Named sections keep the analysts from each inventing their own heading for one subject.
SEED = """\
---
name: hypothesis-builder
description: Rules for planning a DAG that meets a goal and holds each of its criteria, distilled from recorded runs.
---

# Planning a DAG for a goal

## Reading the goal

## Choosing and wiring nodes

## Assertions

## Requesting a tool

## After a rejected round
"""

PROVENANCE = re.compile(r"<!--\s*from:\s*([^>]*?)\s*-->")
PROVENANCE_LINE = re.compile(
    r"^[ \t]*" + PROVENANCE.pattern + r"[ \t]*\n?", re.MULTILINE
)
FENCE = "```"


class Record(BaseModel):
    """One recorded round, as text, with whether its output was right.

    Args:
        id: Names the round, ``<run>:r<n>``, and is what provenance cites.
        label: ``pass`` if the round's output was right by a check independent of the model
            that built or judged it, ``fail`` if that check or the loop rejected it.
        text: The round as the analyst reads it.
    """

    id: str
    label: Label
    text: str


class Draft(BaseModel):
    """An edit as an analyst writes it."""

    op: Op
    section: str = Field(
        description='The heading of the section to change, e.g. "Assertions"; for '
        "add_section, the new heading."
    )
    content: str = Field(
        default="",
        description="Markdown to add, or to put in place of old_text: short imperative "
        "bullets, one rule per bullet.",
    )
    old_text: str = Field(
        default="",
        description="replace_in_section only: the exact text to replace, which must occur "
        "once in that section.",
    )
    after_section: str = Field(
        default="",
        description="add_section only: the section to put it after, or empty for the end.",
    )


class Proposal(BaseModel):
    """What an analyst returns for one record."""

    reasoning: str = Field(
        description="Two or three sentences: the decision in the round that mattered, and "
        "why the edits address it. If there is nothing to add, say why."
    )
    edits: list[Draft] = Field(default=[], max_length=3)


class Merged(Draft):
    """An edit as the merge writes it, naming the numbered input edits it came from."""

    sources: list[int] = Field(
        description="The numbers of the input edits this edit merges or keeps."
    )


class Merge(BaseModel):
    """What a merge returns for a batch of patches."""

    reasoning: str
    edits: list[Merged] = []


class Edit(Draft):
    """An edit with the records it rests on."""

    evidence: list[str]


class Patch(BaseModel):
    """Edits to one skill, from one record or merged from several."""

    edits: list[Edit] = []


class Rejected(BaseModel):
    """An edit that was not applied, and why."""

    edit: Edit
    why: str


class Evolution(BaseModel):
    """The result of ``evolve``.

    Args:
        skill: The skill after the merged patch.
        applied: The edits that changed it.
        rejected: The edits that could not be applied, with the reason.
        proposed: Edits the analysts proposed, before merging.
        silent: Records whose analyst proposed nothing.
        levels: Rounds of merging it took to reach one patch.
        merge_failures: Batches whose merge call failed and were kept unmerged.
        dropped: Merged edits dropped because no input edit supported them.
        tokens: Tokens used by every call.
    """

    skill: str
    applied: list[Edit]
    rejected: list[Rejected]
    proposed: int
    silent: int
    levels: int
    merge_failures: int
    dropped: int
    tokens: int


# --- the file ----------------------------------------------------------------------------


def _key(heading: str) -> str:
    return heading.strip().lstrip("#").strip().casefold()


def _split(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The lines before the first ``##`` heading, then each section's heading and body.

    A ``##`` inside a code fence is text, not a heading.
    """
    head: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(FENCE):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            sections.append((line, []))
        elif sections:
            sections[-1][1].append(line)
        else:
            head.append(line)
    for _, lines in sections:  # The blank lines between sections belong to no section.
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
    return head, sections


def _join(head: list[str], sections: list[tuple[str, list[str]]]) -> str:
    blocks = ["\n".join(head).strip()]
    for heading, lines in sections:
        blocks.append("\n".join([heading, *lines]).strip())
    return "\n\n".join(b for b in blocks if b) + "\n"


def body(skill: str) -> str:
    """The rules as the builder reads them.

    No frontmatter, title or provenance, and no section that holds nothing. Empty when the
    skill has no rules, so a caller can tell there is nothing to show.
    """
    _, sections = _split(PROVENANCE_LINE.sub("", skill))
    kept = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if text:
            kept.append(f"{heading}\n{text}")
    return "\n\n".join(kept)


def digest(skill: str) -> str:
    """A short hash of ``body(skill)``: what the builder read, so a run can name it."""
    return hashlib.sha256(body(skill).encode()).hexdigest()[:12]


def _lines(skill: str) -> int:
    return len(body(skill).splitlines())


def _tagged(content: str, evidence: Sequence[str]) -> list[str]:
    """``content`` as lines, followed by a provenance comment for ``evidence``."""
    lines = content.strip("\n").splitlines()
    ids = sorted(set(evidence))
    return [*lines, f"<!-- from: {', '.join(ids)} -->"] if ids else lines


def _find(sections: list[tuple[str, list[str]]], name: str) -> int | None:
    return next((i for i, (h, _) in enumerate(sections) if _key(h) == _key(name)), None)


def _change(text: str, edit: Edit) -> str:
    """``text`` with ``edit`` applied. Raises ValueError, saying why, if it cannot be."""
    head, sections = _split(text)
    at = _find(sections, edit.section)
    if edit.op == "add_section":
        if at is not None:
            raise ValueError(f"section {edit.section!r} already exists")
        if not edit.content.strip():
            raise ValueError("a new section needs content")
        new = (
            f"## {_key(edit.section) and edit.section.strip().lstrip('#').strip()}",
            [],
        )
        new[1].extend(_tagged(edit.content, edit.evidence))
        after = _find(sections, edit.after_section) if edit.after_section else None
        sections.insert(len(sections) if after is None else after + 1, new)
        return _join(head, sections)
    if at is None:
        raise ValueError(f"no section {edit.section!r}")
    heading, lines = sections[at]
    if edit.op == "delete_section":
        del sections[at]
    elif edit.op == "append_to_section":
        if not edit.content.strip():
            raise ValueError("nothing to append")
        sections[at] = (heading, [*lines, *_tagged(edit.content, edit.evidence)])
    else:  # replace_in_section
        old = "\n".join(lines)
        if not edit.old_text.strip():
            raise ValueError("replace_in_section needs old_text")
        if (n := old.count(edit.old_text)) != 1:
            raise ValueError(f"old_text occurs {n} times in {edit.section!r}, not once")
        # What the old text rested on still holds for what replaces it.
        inherited = [
            i.strip()
            for m in PROVENANCE.findall(edit.old_text)
            for i in m.split(",")
            if i.strip()
        ]
        new = (
            "\n".join(_tagged(edit.content, [*edit.evidence, *inherited]))
            if edit.content.strip()
            else ""
        )
        sections[at] = (heading, old.replace(edit.old_text, new).splitlines())
    return _join(head, sections)


def apply(skill: str, patch: Patch) -> tuple[str, list[Rejected]]:
    """``skill`` with each edit of ``patch`` applied in order, and the edits that could not be.

    An edit is rejected, and the file left as it was, when its section is missing, its
    ``old_text`` is not found exactly once, or the result would pass ``MAX_LINES`` of rules.
    """
    rejected = []
    for edit in patch.edits:
        try:
            changed = _change(skill, edit)
            if _lines(changed) > MAX_LINES and _lines(changed) > _lines(skill):
                raise ValueError(f"over the budget of {MAX_LINES} lines of rules")
        except ValueError as e:
            rejected.append(Rejected(edit=edit, why=str(e)))
        else:
            skill = changed
    return skill, rejected


# --- the agents --------------------------------------------------------------------------

RULES = """\
Write rules the way they will be read: by the builder, a model that turns a goal into a plan
(a DAG of registered nodes: tools make entities, scorers add score columns, filters split
entities by a column) with an assertion for each criterion. Code runs the plan and checks the
assertions, a verifier model can veto the result, and a critique model explains a rejected
round. The builder reads the skill before every plan.

You are shown the current skill and one recorded round. Return a patch to the skill.
- At most three edits, each a few lines: one rule per bullet, in the imperative, saying what to
  do and when, so that it applies to a different goal, gene or input as well.
- Put a rule in the section it belongs to. Use add_section only when no section fits.
- Do not restate a rule the skill already has, even reworded. If it is covered, or the round
  teaches nothing general, return no edits and say why.
- If this round contradicts a rule in the skill, replace that rule, quoting it in old_text.
- Name nodes, assertion branches and config fields as the builder sees them. Never copy a
  sequence, a gene name or a measured value into a rule: say "the first input's score" or
  "the baseline".
- Write from the builder's point of view. It never sees the independent check or how the round
  was labelled, so never mention a grader, a scorer, a label or "the check". Say what to plan
  instead, in terms of nodes, assertions and values it can see.
- Cite only what the round shows. Do not invent what a node does: take it from the round."""

FAIL = f"""\
You maintain a skill from failures. The round you are shown FAILED: the output was wrong by an
independent check, or the loop rejected it; the facts are listed in the round.

Find the decision in the plan that made it fail, such as a node, a wiring, a config value, an
assertion that could not hold what the criterion asked, or a request that was missing or badly
made. Write the rule that would have prevented that decision. Return no edits if you cannot
point to a decision that caused the failure, or if it was a tool or infrastructure fault.

{RULES}"""

PASS = f"""\
You maintain a skill from successes. The round you are shown PASSED: its output was right by an
independent check and the loop accepted it.

Write down only the decisions that made it pass and that the builder would also need for a
different goal: how it read the criteria, which nodes it wired and why, how it asserted each
criterion. Skip anything the goal or the input made easy. Return no edits when nothing here is
worth a rule.

{RULES}"""

MERGE = """\
You merge patches to one skill, proposed independently from different recorded rounds, into
one patch. The edits are numbered. Return the merged edits, each naming in `sources` the
numbers of the input edits it merges or keeps.

- Deduplicate: when edits make the same or a very similar rule, keep one, the most specific
  and best worded, and list every number it stands for.
- Resolve conflicts: when edits contradict each other, keep the one the stronger evidence
  supports, or write one rule that holds both cases, naming when each applies.
- Keep every distinct rule: edits from different rounds usually address different decisions.
- Make no edit of your own. Every merged edit must come from at least one input edit, and an
  edit with no valid source is thrown away.
- Edits are applied in order to one file, so no two may change the same passage, and an
  add_section must come before an edit to the section it adds.
- Keep the operations of the inputs. Stay concise: never longer than the unique edits."""


def _known(known: str) -> str:
    """What the builder is already told or made to do, appended to an agent's instructions.

    The skill is read on top of this, so a rule that repeats it costs the builder's attention
    and adds nothing, and a failure it already prevents is not a lesson.
    """
    if not known.strip():
        return ""
    return (
        "\n\nThe builder already has everything between the <known> tags: its own instructions, "
        "and what the system now does for it. Never write a rule that restates any of it, "
        "however reworded, and treat a failure it already addresses as no lesson at all.\n"
        f"<known>\n{known.strip()}\n</known>"
    )


def analyst_agent(
    model: Model | str, label: Label, known: str = ""
) -> Agent[None, Proposal]:
    """An agent that reads one record of the given label and proposes edits to the skill."""
    return Agent(
        model,
        instructions=(PASS if label == "pass" else FAIL) + _known(known),
        output_type=Proposal,
        retries={"output": 3},
    )


def merge_agent(model: Model | str, known: str = "") -> Agent[None, Merge]:
    """An agent that merges a batch of numbered edits into one patch, dropping known ones."""
    return Agent(
        model,
        instructions=MERGE + _known(known),
        output_type=Merge,
        retries={"output": 3},
    )


def _analyst_prompt(skill: str, record: Record) -> str:
    return (
        f"Current skill:\n<skill>\n{body(skill) or '(no rules yet)'}\n</skill>\n\n"
        f"Sections you may edit: {[h.strip('# ') for h, _ in _split(skill)[1]]}\n\n"
        f"Recorded round {record.id}:\n<round>\n{record.text}\n</round>"
    )


def _merge_prompt(skill: str, patches: Sequence[Patch]) -> str:
    edits = [e for p in patches for e in p.edits]
    listed = "\n\n".join(
        f"[{i}] from {', '.join(e.evidence)}\n"
        f"{e.model_dump_json(exclude={'evidence'}, exclude_defaults=True)}"
        for i, e in enumerate(edits)
    )
    return (
        f"Current skill:\n<skill>\n{body(skill) or '(no rules yet)'}\n</skill>\n\n"
        f"Sections: {[h.strip('# ') for h, _ in _split(skill)[1]]}\n\n"
        f"Edits to merge:\n{listed}"
    )


# --- the distillation --------------------------------------------------------------------


def _chunks[T](items: Sequence[T], size: int) -> list[list[T]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


async def evolve(
    skill: str,
    records: Sequence[Record],
    model: Model | str,
    *,
    batch: int = BATCH,
    workers: int = WORKERS,
    settings: ModelSettings | None = None,
    known: str = "",
) -> Evolution:
    """Distil ``records`` into ``skill``: analyse each, merge in batches, apply the result.

    Every analyst reads the same starting ``skill``, so the order of the records changes
    nothing, which is the point of merging in parallel rather than editing a record at a time.
    A model that cannot give a valid answer is not fatal: that analyst counts as silent, and a
    batch whose merge fails is kept unmerged, so no edit is lost. Any other error, such as a
    missing key or a dead network, stops the run: it must not look like a skill with nothing
    to learn.

    Args:
        skill: The skill to start from; ``SEED`` for a first one.
        records: The labelled rounds to learn from.
        model: The model for every call, a pydantic-ai model or its name.
        batch: Patches per merge call.
        workers: Calls in flight at once.
        settings: Model settings for every call, e.g. the request timeout.
        known: What the builder already has or the system now does for it. Every agent is told
            to leave it out of the skill.
    """
    if batch < 2:
        raise ValueError(f"batch must be at least 2, not {batch}")
    gate = asyncio.Semaphore(workers)
    used = RunUsage()
    # The count is shared by every call, and pydantic-ai caps a shared count at 50 requests.
    # Each call is bounded by its own output retries, and the number of calls by the records.
    limits = UsageLimits(request_limit=None)
    analysts = {label: analyst_agent(model, label, known) for label in ("pass", "fail")}
    merger = merge_agent(model, known)

    async def propose(record: Record) -> Patch:
        async with gate:
            try:
                run = await analysts[record.label].run(
                    _analyst_prompt(skill, record),
                    usage=used,
                    usage_limits=limits,
                    model_settings=settings,
                )
            except (UnexpectedModelBehavior, ContentFilterError):
                return Patch()  # A record the model cannot answer on teaches nothing.
        edits = [Edit(**d.model_dump(), evidence=[record.id]) for d in run.output.edits]
        return Patch(edits=edits)

    patches = list(await asyncio.gather(*(propose(r) for r in records)))
    proposed = sum(len(p.edits) for p in patches)
    silent = sum(not p.edits for p in patches)
    patches = [p for p in patches if p.edits]

    levels = failures = dropped = 0

    async def merge(group: list[Patch]) -> Patch:
        nonlocal failures, dropped
        if len(group) == 1:
            return group[0]
        inputs = [e for p in group for e in p.edits]
        async with gate:
            try:
                run = await merger.run(
                    _merge_prompt(skill, group),
                    usage=used,
                    usage_limits=limits,
                    model_settings=settings,
                )
            except (UnexpectedModelBehavior, ContentFilterError):
                failures += 1  # Unmerged is worse than merged, but never lost.
                return Patch(edits=inputs)
        merged = []
        for m in run.output.edits:
            sources = [i for i in m.sources if 0 <= i < len(inputs)]
            if not sources:
                dropped += 1
                continue
            evidence = sorted({r for i in sources for r in inputs[i].evidence})
            merged.append(Edit(**m.model_dump(exclude={"sources"}), evidence=evidence))
        return Patch(edits=merged)

    while len(patches) > 1:
        levels += 1
        patches = list(
            await asyncio.gather(*(merge(g) for g in _chunks(patches, batch)))
        )
    final = patches[0] if patches else Patch()
    text, rejected = apply(skill, final)
    return Evolution(
        skill=text,
        applied=[e for e in final.edits if all(e is not r.edit for r in rejected)],
        rejected=rejected,
        proposed=proposed,
        silent=silent,
        levels=levels,
        merge_failures=failures,
        dropped=dropped,
        tokens=used.total_tokens,
    )
