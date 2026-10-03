from typing import ClassVar, Literal

from pydantic import Field, field_validator

from node_dag.dna import CODON_TABLE
from node_dag.nodes.base import BaseDecisionConfig, Category
from node_dag.types import Dna


class CodonsAbsentConfig(BaseDecisionConfig):
    """Filter: forward ``sequence`` on yes when none of ``codons`` appears in it.

    The check is in frame: a sequence is split into codons from its first base, and only
    whole codons count. ``AAATGGCCC`` does not contain the codon ``ATG`` even though the
    three letters appear in it, because its codons are AAA, TGG and CCC.

    Use this to check a claim about which codons a sequence still carries. Because it is
    a decision, the branch it takes is recorded, so the answer is evidence rather than an
    opinion.

    Args:
        codons: The codons that must be absent, as three upper-case bases each. At least
            one. Case is normalised and duplicates are dropped.
    """

    name: Literal["codons_absent"] = "codons_absent"
    codons: tuple[str, ...] = Field(min_length=1)
    categories = (Category.FILTER,)
    inputs: ClassVar = {"sequence": Dna}
    forwards = "sequence"
    example: ClassVar = (
        "sequence=ATGTCTTAA with codons=('TCG',) -> yes, forwarding the DNA on "
        "<step>.yes; sequence=ATGTCGTAA -> no. sequence=AAATGGCCC with "
        "codons=('ATG',) -> yes: its codons are AAA, TGG, CCC"
    )

    @field_validator("codons")
    @classmethod
    def _check(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        seen: list[str] = []
        for codon in v:
            upper = codon.upper()
            if upper not in CODON_TABLE:
                raise ValueError(f"Not a codon: {codon!r}")
            if upper not in seen:
                seen.append(upper)
        return tuple(seen)
