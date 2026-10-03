from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Score

ECOLI_ANTI_SD = "ACCTCCTTA"  # The 3' end of the E. coli 16S rRNA, as OSTIR has it.


class OstirExpressionConfig(BaseToolConfig):
    """Score a coding sequence by how fast ribosomes start translating it.

    Runs `OSTIR <https://github.com/barricklab/ostir>`_, which folds the mRNA with
    ViennaRNA and weighs how well its Shine-Dalgarno sequence pairs with the 16S rRNA,
    how far that pairing sits from the start codon, and how much the mRNA has to be
    unfolded to reach it. A sequence is only an mRNA once it has a 5' UTR, so ``utr``
    goes in front of it and only the start codon right after that is scored.

    The score is the translation initiation rate on OSTIR's arbitrary scale, where ten
    times the rate is about 2.3 kcal/mol of binding free energy. Higher means more
    protein from the same mRNA, so filter it with ``at_least``. A sequence whose first
    codon does not initiate scores 0.

    Worth knowing when recoding: synonymous changes keep the protein identical but can
    move this rate by orders of magnitude, because they alter mRNA folding near the
    start. That is one of the ways a recoded gene fails while looking correct, so this
    is the node that turns "it still translates" from an assumption into a measurement.

    Args:
        utr: The 5' UTR to put in front of the sequence: upper-case A, C, G, T. OSTIR
            reads the 35 bases before the start codon, so a shorter UTR leaves it less
            to work with and makes the rate less meaningful.
        anti_sd: The 9 bases at the 3' end of the 16S rRNA that the Shine-Dalgarno
            sequence pairs with. The default is E. coli's; change it to score a
            sequence in another organism, or against an orthogonal ribosome.
    """

    name: Literal["ostir_expression"] = "ostir_expression"
    utr: str
    anti_sd: str = ECOLI_ANTI_SD
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Score
    example: ClassVar = (
        "sequence=ATGTCGTCAGCTTAA with utr=TTCTAGAAGGAGATATACAT -> score 37550.96, the "
        "initiation rate for that start codon. A sequence whose first codon is not a "
        "start codon scores 0. Compare two recodings of the same gene by scoring each."
    )
    intents: ClassVar = (
        "score a sequence by expression",
        "measure the translation rate of a coding sequence",
        "calculate the translation initiation rate with OSTIR",
        "measure ribosome binding site (RBS) strength",
        "check that a recoded gene still translates as well as the original",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks about expression, translation rate, translation "
        "initiation or RBS strength: to measure it, to compare two recodings of the "
        "same gene, or to search for a sequence that expresses more. Higher is more "
        "protein from the same mRNA, so select on it with at_least."
    )
    when_not_to_use: ClassVar = (
        "Do not use to count atoms (dna_atom_score) or to check which codons a "
        "sequence carries (codons_absent). It needs a 5' UTR and a start codon right "
        "after it, so it is meaningless on a non-coding sequence or a fragment that "
        "does not begin at the start codon: such a sequence simply scores 0."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1

    @field_validator("utr", "anti_sd")
    @classmethod
    def _check_bases(cls, v: str) -> str:
        """Both are DNA, upper-case, and neither may be empty."""
        if not v or set(v) - set("ACGT"):
            raise ValueError(f"Not upper-case A, C, G, T: {v!r}")
        return v
