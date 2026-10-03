from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score

ECOLI_ANTI_SD = "ACCTCCTTA"  # The 3' end of the E. coli 16S rRNA, as OSTIR has it.


class OstirExpressionConfig(BaseScoreConfig):
    """Score each coding sequence by how fast ribosomes start translating it.

    Runs `OSTIR <https://github.com/barricklab/ostir>`_, which folds the mRNA with
    ViennaRNA and weighs how well its Shine-Dalgarno sequence pairs with the 16S rRNA,
    how far that pairing sits from the start codon, and how much the mRNA has to be
    unfolded to get there. A sequence is only an mRNA once it has a 5' UTR, so ``utr``
    goes in front of every sequence, and only the start codon right after it is scored.

    Scores:
        expression: The translation initiation rate, on OSTIR's arbitrary scale, where
            ten times the rate is about 2.3 kcal/mol of binding free energy. Higher
            means more protein from the same mRNA, so filter on it with ``at_least``.
            A sequence whose first codon is not a start codon scores 0, since no
            ribosome initiates there.

    Args:
        utr: The 5' UTR to put in front of every sequence: upper-case A, C, G, T.
            OSTIR reads the 35 bases before the start codon, so a shorter UTR leaves
            it less to work with and makes the rate less meaningful.
        anti_sd: The 9 bases at the 3' end of the 16S rRNA, which the Shine-Dalgarno
            sequence pairs with. The default is E. coli's; change it to score a
            sequence in another organism, or an orthogonal ribosome.
    """

    name: Literal["ostir_expression"] = "ostir_expression"
    utr: str
    anti_sd: str = ECOLI_ANTI_SD
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {"expression": Score}

    @field_validator("utr", "anti_sd")
    @classmethod
    def _check_bases(cls, v: str) -> str:
        if not v or set(v) - set("ACGT"):
            raise ValueError(f"Not a non-empty sequence of A, C, G, T: {v!r}")
        return v

    @field_validator("anti_sd")
    @classmethod
    def _check_length(cls, v: str) -> str:
        if len(v) != 9:
            raise ValueError(f"The anti-Shine-Dalgarno sequence is 9 bases: {v!r}")
        return v
