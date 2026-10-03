from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score


class ConstraintCheckConfig(BaseScoreConfig):
    """Check each recoded sequence's hard constraints against the reference it came from.

    These are gates, not objectives. A candidate that changes the protein has failed,
    however good its other scores are, so keep these columns out of any average over
    soft scores and filter on them before comparing anything else.

    All four scores are reported for every input, so a failure is visible rather than
    absent. ``targets_remaining`` and ``targets_unreachable`` are counted separately on
    purpose: the first is what a recoding algorithm could still improve, the second is
    what the genetic code forbids, and adding them together would blame an algorithm
    for the code's own limits.

    One reference serves the whole input list, as ``dna_atom_score`` does, so a step
    checks the candidates of a single gene. Scoring several genes in one DAG needs one
    step per gene, with the instance mapping kept outside sequence-derived ids; see
    ``node_dag.lineage``.

    Scores:
        protein_unchanged: 1.0 when the translated protein equals the reference's,
            0.0 otherwise. Filter with ``at_least`` at 1.0.
        length_unchanged: 1.0 when the sequence is the reference's length, 0.0
            otherwise. An indel breaks the frame even when the protein prefix matches.
        targets_remaining: In-frame occurrences of ``targeted_codons`` left in the
            sequence. 0.0 means this sequence is clear of them. Filter with
            ``at_most`` at 0.0.
        targets_unreachable: Of those remaining, the ones whose amino acid has no
            untargeted synonym, so no synonymous recoding could remove them. A
            sequence with ``targets_remaining`` equal to ``targets_unreachable`` is as
            recoded as the genetic code allows.

    Args:
        reference: The original CDS these candidates were recoded from. Its protein and
            length are the constraint.
        targeted_codons: The codons the recoding was meant to remove. Empty means only
            the protein and length are checked, and both target counts are 0.
    """

    name: Literal["constraint_check"] = "constraint_check"
    reference: Dna
    targeted_codons: tuple[str, ...] = ()
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {
        "protein_unchanged": Score,
        "length_unchanged": Score,
        "targets_remaining": Score,
        "targets_unreachable": Score,
    }
    intents: ClassVar = (
        "check hard constraints of recoded sequences",
        "verify the protein and CDS length are unchanged",
        "count targeted codons still present in-frame",
        "separate constraint violations from soft scores",
        "validate that a recoding preserved the protein",
    )
    when_to_use: ClassVar = (
        "Use after any recoding or mutation step, before comparing soft scores, when "
        "the goal requires the protein to stay unchanged or named codons to be gone."
    )
    when_not_to_use: ClassVar = (
        "Do not use to measure expression, atom count or any quantity to optimise."
    )

    @field_validator("targeted_codons")
    @classmethod
    def _check_codons(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(v)) != len(v):
            raise ValueError(f"Repeated codons: {v!r}")
        for codon in v:
            if len(codon) != 3 or set(codon) - set("ACGT"):
                raise ValueError(f"Not an upper-case DNA codon: {codon!r}")
        return v
