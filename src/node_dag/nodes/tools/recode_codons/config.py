from typing import Annotated, ClassVar, Literal

from pydantic import AfterValidator, Field

from node_dag.dna import codon_set
from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class RecodeCodonsConfig(BaseToolConfig):
    """Remove named codons from each DNA sequence without changing its protein.

    Each in-frame target codon becomes the first synonymous codon that is not a target.
    An amino acid with no such codon (every synonym targeted, or M and W) raises.

    Args:
        targets: The codons to remove, three bases each. At least one.
    """

    name: Literal["recode_codons"] = "recode_codons"
    targets: Annotated[tuple[str, ...], Field(min_length=1), AfterValidator(codon_set)]
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "remove or eliminate specific codons from a DNA sequence",
        "recode a gene so a codon no longer appears, keeping the protein",
        "replace codons with synonymous codons deterministically",
    )
    when_to_use: ClassVar = (
        "Use when the goal names codons to get rid of. It acts on the in-frame codons of "
        "each sequence, so check any other reading frame the goal cares about afterwards."
    )
    when_not_to_use: ClassVar = (
        "Do not use to explore many variants: it makes one fixed recoding. It cannot "
        "recode a codon whose amino acid has no synonym that is not also a target."
    )
