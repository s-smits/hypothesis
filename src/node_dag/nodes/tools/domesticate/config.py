from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.dna import check_motifs
from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna


class DomesticateConfig(BaseToolConfig):
    """Rewrite each coding sequence to remove the given motifs, keeping the protein.

    The sites to remove are usually restriction-enzyme recognition sequences or
    other motifs a synthesis provider rejects. With ``both_strands`` each
    motif's reverse complement is removed too, so the site is gone from both
    strands. Every substitution is synonymous, so the protein is unchanged, and a
    stop codon is never swapped.

    Removal is best-effort, not guaranteed. A motif whose only overlapping
    codons have no synonym or are a stop, like one inside an ATG ATG run, cannot be
    removed synonymously and stays in place. Count what remains with ``motif_count``;
    do not infer removal from this node having run.

    Args:
        motifs: The sites to remove, upper-case A, C, G, T, at least 2 bases.
        both_strands: Remove each motif's reverse complement as well.
        strategy: ``first`` tries overlapping codons and their synonyms in
            genetic-code order, a deterministic smallest change. ``random``
            tries them in a seeded random order, which spreads the edits.
        seed: Seeds ``random``. Ignored by ``first``.
    """

    name: Literal["domesticate"] = "domesticate"
    version: ClassVar[int] = 2  # 2: a stop codon is no longer swapped.
    motifs: tuple[str, ...]
    both_strands: bool = True
    strategy: Literal["first", "random"] = "first"
    seed: int = 0
    categories = (Category.GENERATION,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Dna
    intents: ClassVar = (
        "remove restriction sites or forbidden motifs synonymously",
        "domesticate a coding sequence for DNA synthesis or cloning",
        "rewrite a sequence to eliminate enzyme recognition sites",
        "strip unwanted sequence patterns without changing the protein",
    )
    when_to_use: ClassVar = (
        "Use when the goal names sites or motifs a sequence must not contain, "
        "such as restriction sites for a cloning standard."
    )
    when_not_to_use: ClassVar = (
        "Do not use to count motifs (motif_count), to optimise codon usage "
        "(codon_optimise) or for random synonymous variation."
    )

    @field_validator("motifs")
    @classmethod
    def _check_motifs(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return check_motifs(v)
