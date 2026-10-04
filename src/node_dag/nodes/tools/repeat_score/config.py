from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import NucleicAcid, Score


class RepeatScoreConfig(BaseScoreConfig):
    """Score each sequence's internal repeats, the usual limit on DNA synthesis.

    Repeated stretches are what makes a sequence hard to assemble and prone to
    recombining once it is in a cell, and they are a constraint no other score
    here sees: ``motif_count`` only finds the motifs it is given, and its
    homopolymer run is the special case of a one-base repeat. Recoding changes
    repeats as a side effect, so this is worth scoring whenever synthesis is
    part of the goal.

    RNA inputs are read as their DNA, so the reverse complement means the same
    for both. Occurrences may overlap.

    Scores:
        max_repeat: The length of the longest substring that occurs at least
            twice. Filter with ``at_most``; vendors commonly refuse repeats
            beyond roughly 20 bases, though the real limit depends on the
            vendor and the construct.
        max_inverted_repeat: The length of the longest substring whose reverse
            complement also occurs, i.e. the longest hairpin the sequence can
            form with itself. A substring that is its own reverse complement
            counts, since it pairs with itself.
        repeat_fraction: The fraction of positions inside some substring of at
            least ``min_length`` bases that occurs more than once, 0 to 1. This
            says how much of the sequence is repetitive, where ``max_repeat``
            says only how bad the single worst stretch is.

    Args:
        min_length: The shortest repeat that counts towards
            ``repeat_fraction``. It does not affect the two maxima, which are
            searched over every length.
    """

    name: Literal["repeat_score"] = "repeat_score"
    min_length: int = Field(default=8, ge=2)
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": NucleicAcid}
    output: ClassVar = {
        "max_repeat": Score,
        "max_inverted_repeat": Score,
        "repeat_fraction": Score,
    }
    intents: ClassVar = (
        "score internal repeats for DNA synthesis feasibility",
        "measure direct and inverted repeats or hairpin potential",
        "check how repetitive a designed sequence is",
    )
    when_to_use: ClassVar = (
        "Use when the goal involves ordering or assembling the sequences, or "
        "to keep a recoding from making a gene more repetitive."
    )
    when_not_to_use: ClassVar = (
        "Do not use for named sites or homopolymers alone (motif_count), or "
        "for mRNA folding energy (mrna_fold_energy, mrna_5prime_mfe)."
    )
