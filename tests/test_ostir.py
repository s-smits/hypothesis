import pytest
from pydantic import ValidationError

from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.ostir_expression.function import OstirExpression
from node_dag.types import Dna

CDS = Dna(sequence="ATGGCTCTGAAATAA")  # M A L K *
STRONG = "TTCTAGAAAGGAGGTAAAAAA"  # AAGGAGG, six bases before the start codon.
WEAK = "TTCTAGACCTCCTTATAAAAA"  # No Shine-Dalgarno to pair with the 16S rRNA.


def rate(utr: str, cds: Dna = CDS, **kwargs) -> float:
    (scores,) = OstirExpression(OstirExpressionConfig(utr=utr, **kwargs)).run(
        sequence=[cds]
    )
    return scores["expression"].value


def test_a_shine_dalgarno_before_the_start_codon_raises_the_rate():
    assert rate(STRONG) > 100 * rate(WEAK)


def test_a_sequence_that_does_not_start_with_a_start_codon_scores_zero():
    # OSTIR finds no initiation at the first codon, so no ribosome starts there.
    assert rate(STRONG, Dna(sequence="GCTCTGAAATAA")) == 0.0


def test_the_rate_is_the_same_every_time():
    # Node results are cached by config and inputs, so the same run must repeat.
    assert rate(STRONG) == rate(STRONG)


def test_another_anti_shine_dalgarno_scores_the_same_sequence_differently():
    # An orthogonal ribosome does not read an E. coli Shine-Dalgarno.
    assert rate(STRONG, anti_sd="ACCTCCTTA") != rate(STRONG, anti_sd="TCCTCCTTA")


def test_the_score_column_says_which_utr_and_ribosome_made_it():
    config = OstirExpressionConfig(utr=STRONG)
    assert config.columns() == {
        "expression": f"ostir_expression__{config.config_hash}__expression"
    }
    for other in (
        OstirExpressionConfig(utr=WEAK),
        OstirExpressionConfig(utr=STRONG, anti_sd="TCCTCCTTA"),
    ):
        assert other.config_hash != config.config_hash


@pytest.mark.parametrize(
    "kwargs",
    [
        {"utr": "TTCTAGAU"},  # RNA, not DNA.
        {"utr": ""},
        {"utr": STRONG, "anti_sd": "ACCTCCTT"},  # Eight bases, not nine.
    ],
)
def test_a_utr_or_ribosome_that_is_not_dna_is_rejected(kwargs):
    with pytest.raises(ValidationError):
        OstirExpressionConfig(**kwargs)
