import json
import re

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from node_dag import skills
from node_dag.skills import (
    MAX_LINES,
    SEED,
    Edit,
    Op,
    Patch,
    Record,
    apply,
    body,
    digest,
    evolve,
)


def _edit(op: Op = "append_to_section", section: str = "Assertions", **kw) -> Edit:
    kw.setdefault("content", "- Assert the filter's yes branch.")
    kw.setdefault("evidence", ["a:r1"])
    return Edit(op=op, section=section, **kw)


def _apply(skill: str, *edits: Edit) -> tuple[str, list[str]]:
    text, rejected = apply(skill, Patch(edits=list(edits)))
    return text, [r.why for r in rejected]


# --- the file ------------------------------------------------------------------------


def test_the_seed_has_no_rules_so_the_builder_reads_nothing():
    assert body(SEED) == ""


def test_append_keeps_the_rule_and_records_where_it_came_from():
    text, why = _apply(SEED, _edit(evidence=["b:r2", "a:r1"]))
    assert why == []
    assert "- Assert the filter's yes branch.\n<!-- from: a:r1, b:r2 -->" in text
    assert body(text) == "## Assertions\n- Assert the filter's yes branch."


def test_provenance_does_not_change_what_the_builder_read():
    one, _ = _apply(SEED, _edit(evidence=["a:r1"]))
    two, _ = _apply(SEED, _edit(evidence=["a:r1", "z:r9"]))
    assert one != two
    assert digest(one) == digest(two)
    assert digest(one) != digest(SEED)


def test_replace_needs_the_old_text_exactly_once_and_inherits_its_evidence():
    skill, _ = _apply(
        SEED, _edit(content="- Old rule.\n- Old rule.", evidence=["a:r1"])
    )
    text, why = _apply(skill, _edit("replace_in_section", old_text="- Old rule."))
    assert why == ["old_text occurs 2 times in 'Assertions', not once"]
    assert text == skill

    skill, _ = _apply(SEED, _edit(content="- Old rule.", evidence=["a:r1"]))
    text, why = _apply(
        skill,
        _edit(
            "replace_in_section",
            old_text="- Old rule.\n<!-- from: a:r1 -->",
            content="- New rule.",
            evidence=["b:r2"],
        ),
    )
    assert why == []
    assert "<!-- from: a:r1, b:r2 -->" in text
    assert "Old rule" not in text


def test_an_edit_to_a_missing_section_or_a_duplicate_one_is_rejected():
    _, why = _apply(SEED, _edit(section="Nowhere"))
    assert why == ["no section 'Nowhere'"]
    _, why = _apply(SEED, _edit("add_section", section="Assertions"))
    assert why == ["section 'Assertions' already exists"]
    _, why = _apply(SEED, _edit("add_section", section="New", content=" "))
    assert why == ["a new section needs content"]


def test_a_section_can_be_added_after_another_and_deleted():
    text, why = _apply(
        SEED, _edit("add_section", section="## Ordering", after_section="assertions")
    )
    assert why == []
    names = re.findall(r"^## (.*)$", text, re.MULTILINE)
    assert names == [
        "Reading the goal",
        "Choosing and wiring nodes",
        "Assertions",
        "Ordering",
        "Requesting a tool",
        "After a rejected round",
    ]
    text, why = _apply(text, _edit("delete_section", section="Ordering", content=""))
    assert why == [] and "Ordering" not in text


def test_a_heading_inside_a_code_fence_is_not_a_section():
    skill, _ = _apply(
        SEED, _edit(content="- Write:\n```\n## Not a heading\n```", evidence=["a:r1"])
    )
    _, why = _apply(skill, _edit(section="Not a heading"))
    assert why == ["no section 'Not a heading'"]
    assert "## Not a heading" in body(skill)


def test_a_rule_that_would_pass_the_line_budget_is_rejected_and_deletion_frees_room():
    full = "\n".join(f"- Rule {i}." for i in range(MAX_LINES - 1))
    skill, why = _apply(SEED, _edit(content=full))
    assert why == []
    text, why = _apply(skill, _edit(content="- One\n- Two"))
    assert why == [f"over the budget of {MAX_LINES} lines of rules"]
    assert text == skill
    text, why = _apply(
        skill,
        _edit("delete_section", content=""),
        _edit("add_section", section="Fresh", content="- One"),
    )
    assert why == [] and body(text) == "## Fresh\n- One"


# --- the distillation ----------------------------------------------------------------


def _prompt(messages: list[ModelMessage]) -> str:
    return next(
        str(p.content)
        for m in messages
        for p in m.parts
        if isinstance(p, UserPromptPart)
    )


def _call(info: AgentInfo, args: dict) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


class Script:
    """A model that proposes one rule per record and merges by rule text."""

    def __init__(
        self, silent: tuple[str, ...] = (), merge_raises: bool = False
    ) -> None:
        self.silent = silent
        self.merge_raises = merge_raises
        self.analysed: list[str] = []
        self.merged: list[str] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        """Answer as the analyst or the merge, whichever the prompt is for."""
        prompt = _prompt(messages)
        if "Edits to merge:" in prompt:
            if self.merge_raises:
                raise UnexpectedModelBehavior("no valid merge")
            self.merged.append(prompt)
            listed = re.findall(r"^\[(\d+)\] from (.*)$", prompt, re.MULTILINE)
            rules = re.findall(r'"content":"(.*?)"', prompt)
            keep: dict[str, list[int]] = {}
            for (n, _), rule in zip(listed, rules, strict=True):
                keep.setdefault(rule, []).append(int(n))
            edits = [
                {
                    "op": "append_to_section",
                    "section": "Assertions",
                    "content": rule,
                    "sources": ns,
                }
                for rule, ns in keep.items()
            ]
            edits.append(  # Nothing supports this one, so it must not survive.
                {
                    "op": "append_to_section",
                    "section": "Assertions",
                    "content": "- Invented.",
                    "sources": [99],
                }
            )
            return _call(info, {"reasoning": "merged", "edits": edits})
        found = re.search(r"Recorded round (\S+):", prompt)
        assert found
        rid = found.group(1)
        self.analysed.append(rid)
        if rid in self.silent:
            return _call(info, {"reasoning": "nothing general", "edits": []})
        rule = f"- Rule for {rid.split(':')[0][0]}."  # Records of one run share a rule.
        return _call(
            info,
            {
                "reasoning": "r",
                "edits": [
                    {
                        "op": "append_to_section",
                        "section": "Assertions",
                        "content": rule,
                    }
                ],
            },
        )


def _records(*ids: str) -> list[Record]:
    return [Record(id=i, label="fail", text=f"round {i}") for i in ids]


async def test_one_record_is_one_analyst_call_and_no_merge():
    script = Script()
    out = await evolve(SEED, _records("a:r1"), FunctionModel(script))
    assert script.analysed == ["a:r1"] and script.merged == []
    assert out.levels == 0 and out.proposed == 1 and out.silent == 0
    assert "<!-- from: a:r1 -->" in out.skill
    assert [e.evidence for e in out.applied] == [["a:r1"]]


async def test_a_rule_that_recurs_is_merged_into_one_with_all_its_evidence():
    script = Script()
    ids = ("a:r1", "a:r2", "b:r1", "a:r3", "c:r1", "a:r4", "b:r2")
    out = await evolve(SEED, _records(*ids), FunctionModel(script), batch=3)
    assert sorted(script.analysed) == sorted(ids)
    assert out.levels == 2  # seven patches, then three, then one
    assert out.dropped == len(script.merged)  # the invented edit, once per merge call
    assert out.rejected == []
    rules = body(out.skill).splitlines()[1:]
    assert sorted(rules) == ["- Rule for a.", "- Rule for b.", "- Rule for c."]
    comment = re.search(r"- Rule for a\.\n<!-- from: (.*?) -->", out.skill)
    assert comment and comment.group(1) == "a:r1, a:r2, a:r3, a:r4"


async def test_the_order_of_the_records_changes_nothing():
    ids = ("a:r1", "b:r1", "a:r2", "c:r1")
    one = await evolve(SEED, _records(*ids), FunctionModel(Script()), batch=2)
    two = await evolve(SEED, _records(*reversed(ids)), FunctionModel(Script()), batch=2)
    assert sorted(body(one.skill).splitlines()) == sorted(body(two.skill).splitlines())


async def test_a_silent_analyst_counts_and_a_failed_merge_loses_nothing():
    script = Script(silent=("a:r2",), merge_raises=True)
    out = await evolve(SEED, _records("a:r1", "a:r2", "b:r1"), FunctionModel(script))
    assert out.silent == 1 and out.merge_failures == 1
    assert sorted(e.evidence[0] for e in out.applied) == ["a:r1", "b:r1"]


async def test_an_analyst_that_cannot_answer_teaches_nothing_and_does_not_stop_the_rest():
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if "round bad" in _prompt(messages):
            return ModelResponse(parts=[TextPart("no tool call, ever")])
        return Script()(messages, info)

    out = await evolve(SEED, _records("bad", "a:r1"), FunctionModel(script))
    assert out.silent == 1 and [e.evidence for e in out.applied] == [["a:r1"]]


async def test_every_analyst_reads_the_starting_skill_and_the_label_picks_the_prompt():
    seen: list[tuple[str, str]] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append((info.instructions or "", _prompt(messages)))
        return _call(info, {"reasoning": "none", "edits": []})

    start, _ = _apply(SEED, _edit(content="- Existing rule."))
    records = [
        Record(id="a:r1", label="pass", text="x"),
        Record(id="b:r1", label="fail", text="y"),
    ]
    await evolve(start, records, FunctionModel(script))
    assert all("- Existing rule." in prompt for _, prompt in seen)
    assert all("<!--" not in prompt for _, prompt in seen)
    by_label = {("PASSED" in ins, "FAILED" in ins) for ins, _ in seen}
    assert by_label == {(True, False), (False, True)}


async def test_an_error_that_is_not_the_models_stops_the_run_instead_of_looking_like_silence():
    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError("no network")

    with pytest.raises(ConnectionError):
        await evolve(SEED, _records("a:r1"), FunctionModel(script))


async def test_a_batch_of_one_would_never_finish_and_is_refused():
    with pytest.raises(ValueError, match="at least 2"):
        await evolve(SEED, _records("a:r1"), FunctionModel(Script()), batch=1)


def test_the_merge_prompt_numbers_the_edits_it_is_given():
    patch = Patch(edits=[_edit(evidence=["a:r1"]), _edit(evidence=["b:r1"])])
    prompt = skills._merge_prompt(SEED, [patch])
    assert re.findall(r"^\[(\d+)\] from (.*)$", prompt, re.MULTILINE) == [
        ("0", "a:r1"),
        ("1", "b:r1"),
    ]
    assert json.loads(prompt.split("\n")[-1])["op"] == "append_to_section"


async def test_more_than_fifty_calls_are_not_cut_off_by_the_shared_request_limit():
    ids = [f"{chr(97 + i % 20)}:r{i}" for i in range(60)]
    out = await evolve(SEED, _records(*ids), FunctionModel(Script()), batch=5)
    assert out.proposed == 60 and out.silent == 0 and out.merge_failures == 0


# --- what the builder already has -----------------------------------------------------


async def _instructions(known: str) -> list[str]:
    """What the pass analyst, the fail analyst and the merge are told, in that order."""
    seen: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        return _call(info, {"reasoning": "none", "edits": []})

    model = FunctionModel(script)
    await skills.analyst_agent(model, "pass", known).run("round")
    await skills.analyst_agent(model, "fail", known).run("round")
    await skills.merge_agent(model, known).run("edits")
    return seen


async def test_what_the_builder_already_has_goes_to_every_agent_and_nothing_when_there_is_none():
    for text in await _instructions("Never assert on a key."):
        assert "<known>\nNever assert on a key.\n</known>" in text
        assert "Never write a rule that restates" in text
    for text in await _instructions(" \n"):
        assert "<known>" not in text


async def test_every_call_of_a_run_is_told_what_is_known():
    seen: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        return Script()(messages, info)

    await evolve(
        SEED,
        _records("a:r1", "b:r1", "c:r1"),
        FunctionModel(script),
        batch=2,
        known="Plans are checked for a result.",
    )
    assert len(seen) == 5  # three analysts, then two merges
    assert all("Plans are checked for a result." in text for text in seen)
