"""search_nodes is how the builder finds a node by the job it needs done.

The flat list works at twelve nodes. These tests are about what has to keep working as
the shelf grows: that a search for the job ranks the node that does that job first. The
pair that matters most is recode_codons against mutate_synonymous, because "change
codons but keep the protein" describes both and only one of them can be aimed at a
named codon.
"""

import json

import pytest

from node_dag.agent import NO_MATCH, NODES, list_nodes, search_nodes


def _order(text):
    """The node names in the order search_nodes returned them."""
    return [line.split(" ", 1)[0] for line in text.splitlines() if line]


def _rank(text, name):
    """Where ``name`` came in the results, or None if it is not there."""
    names = _order(text)
    return names.index(name) if name in names else None


@pytest.mark.parametrize(
    "query",
    [
        "remove a specific codon",
        "remove every TAG codon from the sequence",
        "replace named codons keeping the protein",
        "free up a codon for reassignment",
    ],
)
def test_a_targeted_codon_job_ranks_recode_above_mutate(query):
    """The wrong turn the builder can take: random mutation cannot remove a named codon."""
    text = search_nodes(query)
    recode, mutate = _rank(text, "recode_codons"), _rank(text, "mutate_synonymous")
    assert recode is not None, text
    assert mutate is None or recode < mutate, text


def test_mutate_synonymous_says_it_cannot_target_a_codon():
    """Whichever node the agent reaches for, the contract must redirect it."""
    contract = NODES["mutate_synonymous"].contract()
    assert "random" in contract["when_not_to_use"].lower()
    assert "recode_codons" in contract["when_not_to_use"]


@pytest.mark.parametrize(
    "query", ["expression", "translation rate", "ribosome binding site strength"]
)
def test_an_expression_job_finds_ostir(query):
    """The scorer is found by what it measures, not by its name."""
    assert "ostir_expression" in _order(search_nodes(query))


def test_a_search_for_an_absent_job_says_so():
    """No match is a clear answer, not an empty string or the whole catalogue."""
    assert search_nodes("book a flight to Berlin") == NO_MATCH
    assert search_nodes("crystallography beamline") == NO_MATCH


@pytest.mark.parametrize("query", ["", "   "])
def test_an_empty_query_lists_everything(query):
    """With nothing to rank on, fall back to the catalogue rather than refusing."""
    assert search_nodes(query) == list_nodes()
    assert set(_order(search_nodes(query))) == set(NODES)


def test_a_result_carries_the_contract_and_the_docstring():
    """A hit is usable on its own: the agent gets ports, kinds and guidance with it."""
    text = search_nodes("score expression")
    line = next(ln for ln in text.splitlines() if ln.startswith("ostir_expression "))
    contract = json.loads(line.split(" ", 1)[1].rsplit(": ", 1)[0])
    assert contract == NODES["ostir_expression"].contract()
    assert contract["inputs"] == {"sequence": "dna"}
    assert contract["intents"] and contract["when_to_use"]
    assert line.endswith(NODES["ostir_expression"].__doc__.splitlines()[0])


def test_intents_outweigh_a_passing_mention_in_prose():
    """Weighting exists so the node whose job this is beats one that merely mentions it."""
    text = search_nodes("remove a specific codon, keeping the protein")
    assert _order(text)[0] == "recode_codons", text


def test_a_conversion_is_found_in_the_right_direction():
    """The search counts words, so it cannot see which way a conversion runs.

    rna_back_transcribe once won "turn DNA into RNA" -- the opposite direction -- because
    naming DNA in every intent out-scored the node that actually does it. Both nodes are
    phrased from their own input side to keep that from coming back.
    """
    assert _order(search_nodes("turn DNA into RNA"))[0] == "dna_transcribe"
    assert _order(search_nodes("transcribe this coding sequence"))[0] == "dna_transcribe"
    assert _order(search_nodes("convert RNA back to DNA"))[0] == "rna_back_transcribe"
    assert (
        _order(search_nodes("my value is RNA but the next node wants DNA"))[0]
        == "rna_back_transcribe"
    )
