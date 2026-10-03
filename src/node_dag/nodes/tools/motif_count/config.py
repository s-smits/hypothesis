from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.dna import check_motifs
from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import NucleicAcid, Score


class MotifCountConfig(BaseScoreConfig):
    """Count unwanted motifs and homopolymer runs in each sequence.

    These are constraint counts, not objectives: a sequence that still carries
    a restriction site has failed that constraint whatever its other scores.
    With ``both_strands`` each motif's reverse complement is counted too, so a
    site shows up whichever strand it sits on. RNA inputs are read as their
    DNA, so the same motifs apply to both.

    Scores:
        motifs: Total occurrences of the configured sites; overlapping
            occurrences each count. Filter with ``at_most`` at 0 to keep only
            clean sequences.
        max_homopolymer: The longest run of a single base, a synthesis
            constraint of its own.

    Args:
        motifs: The sites to count, upper-case A, C, G, T, at least 2 bases.
        both_strands: Also count each motif's reverse complement.
    """

    name: Literal["motif_count"] = "motif_count"
    motifs: tuple[str, ...]
    both_strands: bool = True
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": NucleicAcid}
    output: ClassVar = {"motifs": Score, "max_homopolymer": Score}
    intents: ClassVar = (
        "count restriction sites or forbidden motifs in sequences",
        "check sequences for unwanted sites on either strand",
        "measure homopolymer runs for synthesis feasibility",
    )
    when_to_use: ClassVar = (
        "Use to verify a domesticate run or to filter out sequences that still "
        "carry a named site or a long single-base run."
    )
    when_not_to_use: ClassVar = (
        "Do not use to remove motifs (domesticate) or to measure base "
        "composition (gc_content)."
    )

    @field_validator("motifs")
    @classmethod
    def _check_motifs(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return check_motifs(v)
