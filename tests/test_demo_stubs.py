"""The demo's stubs must report what happened, not invent it.

They used to be constants. A canned verdict said the protein was MSSA* for a run whose
protein was MYEA, and a canned critique blamed a surviving codon for an input-name
mismatch -- then sent the next round after the wrong thing. A stub cannot reason, but it
can refrain from making things up.
"""


from node_dag.plan import Criterion
from temporal.hypothesis.activities import resolve_plan
from temporal.hypothesis.models import CritiqueInput, VerifyInput, VerifyView
from temporal.run_demo import BLOCKED, STUBS, critique_attempt_stub, stub_verify


def _view(held: dict[str, bool], error: str | None = None) -> VerifyView:
    return VerifyView(
        goal="remove every TCG codon",
        criteria=[Criterion(id="no_tcg", claim="no TCG codon remains")],
        inputs={},
        hypothesis="recode and check",
        expected="the check takes its yes branch",
        held=held,
        error=error,
    )


async def test_the_verifier_agrees_only_when_the_assertions_held():
    agreed = await stub_verify(VerifyInput(view=_view({"clean.yes": True}), model="demo"))
    assert agreed.agrees and agreed.score == 1.0
    assert "held" in agreed.reason

    refused = await stub_verify(
        VerifyInput(view=_view({"clean.yes": False}), model="demo")
    )
    assert not refused.agrees and refused.score == 0.0
    assert "clean.yes" in refused.reason


async def test_the_verifier_says_so_when_nothing_was_asserted():
    out = await stub_verify(VerifyInput(view=_view({}), model="demo"))
    assert not out.agrees
    assert "asserted nothing" in out.reason


async def test_the_verifier_admits_it_is_a_stub():
    """Nobody should mistake a reported check for a judgement."""
    out = await stub_verify(VerifyInput(view=_view({"clean.yes": True}), model="demo"))
    assert "stubbed" in out.reason.lower()


async def test_the_critic_repeats_the_error_it_was_given():
    """The canned one blamed a codon for an input mismatch. This one cannot."""
    out = await critique_attempt_stub(
        CritiqueInput(view=_view({}, error="the plan declares inputs x"), model="demo")
    )
    assert "the plan declares inputs x" in out.critique.diagnosis
    assert out.critique.root_cause == "node_raised"


async def test_the_critic_names_the_assertion_that_failed():
    out = await critique_attempt_stub(
        CritiqueInput(view=_view({"clean.yes": False}), model="demo")
    )
    assert "clean.yes" in out.critique.diagnosis
    assert "clean.yes" in out.critique.evidence


def test_the_demo_uses_the_real_resolver():
    """Whether a node exists is a fact, so the demo must not fake it."""
    assert resolve_plan in STUBS


def test_the_demo_still_has_something_genuinely_missing():
    """Otherwise the blocked path could not be demonstrated honestly."""
    from node_dag.agent import NODES

    assert BLOCKED.node_names() - set(NODES), (
        "every node the demo plan wants now exists, so it can no longer show a block"
    )
