from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class RecodeTargetedConfig(BaseToolConfig):
    """Recode every in-frame occurrence of the targeted codons, keeping the protein.

    Unlike ``mutate_synonymous``, which swaps a count of randomly chosen codons, this
    visits every in-frame occurrence of ``targeted_codons`` and replaces it with a
    synonymous codon that is not itself targeted. It is the baseline for the
    target-codon-elimination task: removing a codon's every use so the codon can be
    reassigned.

    Elimination is not guaranteed and is not claimed. A targeted codon stays in place
    when its amino acid has no untargeted synonym, as for the single-codon amino acids
    methionine (ATG) and tryptophan (TGG), or when every synonym is itself targeted.
    Score the output with ``constraint_check`` to count what remains; do not infer
    elimination from this node having run.

    The protein and the CDS length are preserved by construction, since only whole
    codons are substituted for synonyms of the same amino acid. ``constraint_check``
    verifies that independently rather than trusting it.

    Args:
        targeted_codons: The codons to remove, as upper-case DNA triplets.
        strategy: How to choose a replacement among the untargeted synonyms.
            ``random`` picks one, seeded by ``seed`` and the sequence id, so a
            sequence's recoding does not depend on what else is in the batch.
            ``first`` takes the first in the genetic code's own codon order, which
            ignores ``seed`` and makes the node fully deterministic.
        seed: Seeds the ``random`` strategy. Ignored by ``first``.
    """

    name: Literal["recode_targeted"] = "recode_targeted"
    targeted_codons: tuple[str, ...]
    strategy: Literal["random", "first"] = "random"
    seed: int = 0
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "remove or eliminate specific codons from coding sequences",
        "recode every occurrence of a targeted codon synonymously",
        "free a codon for reassignment by replacing all its uses",
        "baseline for target-codon-elimination recoding",
    )
    when_to_use: ClassVar = (
        "Use when the goal names particular codons to remove, eliminate, replace or "
        "free, rather than asking for random synonymous variation."
    )
    when_not_to_use: ClassVar = (
        "Do not use to generate a pool of random candidates for optimisation; "
        "mutate_synonymous does that. Do not use to check what remains; "
        "constraint_check does that."
    )

    @field_validator("targeted_codons")
    @classmethod
    def _check_codons(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("No targeted codons given.")
        if len(set(v)) != len(v):
            raise ValueError(f"Repeated codons: {v!r}")
        for codon in v:
            if len(codon) != 3 or set(codon) - set("ACGT"):
                raise ValueError(f"Not an upper-case DNA codon: {codon!r}")
        return v
