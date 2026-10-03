"""The node that measures what "the protein is unchanged" does not.

Kept apart from test_nodes.py because OSTIR folds the mRNA with ViennaRNA, so each call
costs about a second -- far slower than every other node test in the suite.
"""

import pytest

from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.ostir_expression.function import OstirExpression
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.types import Dna

# A Shine-Dalgarno sequence and spacer strong enough for OSTIR to score against.
UTR = "TTCTAGAAGGAGATATACAT"
GENE = Dna(sequence="ATGTCGTCAGCTTAA")  # MSSA*


def _score(sequence: Dna, **config: object) -> float:
    node = OstirExpression(OstirExpressionConfig(utr=UTR, **config))
    return node.run(sequence=sequence).value


def test_a_coding_sequence_scores_above_zero():
    assert _score(GENE) > 0


def test_a_sequence_that_does_not_start_scores_zero():
    """No ribosome initiates there, so there is no rate to report."""
    assert _score(Dna(sequence="TTTTCGTCAGCTTAA")) == 0.0


def test_the_same_sequence_always_scores_the_same():
    """The node is cached by config and inputs, so it had better be deterministic."""
    assert _score(GENE) == _score(GENE)


def test_a_synonymous_recoding_can_change_expression_by_an_order_of_magnitude():
    """Why "the protein is unchanged" is necessary and nowhere near sufficient.

    recode_codons produces a sequence that is protein-identical and passes every
    criterion about codon content -- and translates several times worse, because
    synonymous changes move the mRNA folding near the start codon. A loop that could
    only check the protein would call this a success.
    """
    recoded = RecodeCodons(RecodeCodonsConfig(targets=("TCG", "TCA"))).run(sequence=GENE)
    assert recoded.protein() == GENE.protein(), "the recoding must be synonymous"
    assert recoded.sequence != GENE.sequence

    before, after = _score(GENE), _score(recoded)
    assert before > 0 and after > 0
    assert before / after > 2, (
        f"expected the recoding to shift expression materially, got {before} -> {after}"
    )


def test_the_anti_sd_sequence_changes_the_score():
    """Scoring against a different ribosome gives a different answer, as it should."""
    assert _score(GENE) != _score(GENE, anti_sd="ACCTCCTTT")


@pytest.mark.parametrize("bad", ["", "ATGX", "atg"])
def test_the_utr_must_be_upper_case_dna(bad):
    with pytest.raises(ValueError, match="Not upper-case"):
        OstirExpressionConfig(utr=bad)
