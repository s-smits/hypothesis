from typing import ClassVar, Literal

from pydantic import Field, field_validator

from node_dag.dna import CODON_TABLE
from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class RecodeCodonsConfig(BaseToolConfig):
    """Remove named codons from a DNA sequence without changing the protein.

    Every in-frame occurrence of a target codon is swapped for a synonymous codon that
    is not itself a target, so the amino acid sequence is unchanged and no target codon
    remains. Unlike ``mutate_synonymous`` the choice is targeted, not random: name the
    codons you want gone. Codons that are not targets pass through untouched.

    An amino acid whose every synonymous codon is targeted cannot be recoded and is an
    error, not a silent pass-through. ``ATG`` (M) and ``TGG`` (W) are the only codon for
    their amino acid, so targeting either always fails.

    Args:
        targets: The codons to remove, as three upper-case bases each. At least one.
            Case is normalised and duplicates are dropped.
    """

    name: Literal["recode_codons"] = "recode_codons"
    targets: tuple[str, ...] = Field(min_length=1)
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    example: ClassVar = (
        "sequence=ATGTCGTAA with targets=('TCG',) -> ATGTCTTAA: the TCG codon becomes "
        "TCT, the first synonym of S that is not a target, and the protein stays MS*"
    )
    intents: ClassVar = (
        "remove or replace specific named codons, keeping the protein",
        "remove a specific codon from a DNA sequence",
        "recode a gene so a given codon no longer appears",
        "reassign or free up a codon for genetic code expansion",
        "swap targeted codons for synonyms deterministically",
    )
    when_to_use: ClassVar = (
        "Use to remove or replace specific named codons, keeping the protein. This is "
        "the node whenever the goal names the codons: remove every TCG, leave no TAG, "
        "free up a codon. Every in-frame occurrence is swapped for a synonym that is "
        "not itself a target, so the result is deterministic and no target codon "
        "remains."
    )
    when_not_to_use: ClassVar = (
        "Do not use when the goal names no codon and only asks for a sequence that "
        "scores better; mutate_synonymous searches codon space at random for that. Do "
        "not use it to check whether a codon is absent: it changes the sequence rather "
        "than deciding about it, so pair it with codons_absent for the evidence. "
        "Targeting ATG or TGG always fails: each is the only codon for its amino acid."
    )

    @field_validator("targets")
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
