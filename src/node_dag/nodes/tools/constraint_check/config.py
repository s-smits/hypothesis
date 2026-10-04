from typing import Annotated, ClassVar, Literal, Self

from pydantic import Field, field_validator, model_validator

from node_dag.dna import codons
from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import Dna, Score


class ConstraintCheckConfig(BaseScoreConfig):
    """Check each recoded sequence's hard constraints against the reference it came from.

    These are gates, not objectives. A candidate that changes the protein has failed,
    however good its other scores are, so keep these columns out of any average over
    soft scores and filter on them before comparing anything else.

    All six scores are reported for every input, so a failure is visible rather than
    absent. ``targets_remaining`` and ``targets_unreachable`` are counted separately on
    purpose: the first is what a recoding algorithm could still improve, the second is
    what the genetic code forbids, and adding them together would blame an algorithm
    for the code's own limits.

    ``immutable`` pins named codon positions, which the protein check cannot do. The
    three stop codons all translate to ``*``, so swapping one keeps the protein and
    passes ``protein_unchanged`` while changing a codon an optimiser may have been
    forbidden to touch. A comparison against an exact optimum then credits a candidate
    for a move the optimum could not make.

    One reference serves the whole input list, as ``dna_atom_score`` does, so a step
    checks the candidates of a single gene. Scoring several genes in one DAG needs one
    step per gene, with the instance mapping kept outside sequence-derived ids; see
    ``node_dag.lineage``.

    Scores:
        protein_unchanged: 1.0 when the translated protein equals the reference's,
            0.0 otherwise. Filter with ``at_least`` at 1.0.
        length_unchanged: 1.0 when the sequence is the reference's length, 0.0
            otherwise. An indel breaks the frame even when the protein prefix matches.
        immutable_unchanged: 1.0 when the codon at every index in ``immutable`` equals
            the reference's, counting a synonymous stop as a change; 0.0 otherwise.
            With no indices it is 1.0, because nothing was asked for, so read it with
            ``immutable_checked`` rather than alone.
        immutable_checked: How many indices ``immutable`` named. 0.0 means
            ``immutable_unchanged`` held vacuously and pinned nothing. Filter on this
            as well when the goal names positions to keep, or a step that forgot
            ``immutable`` passes a gate that checked nothing.
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
        targeted_codons: The codons the recoding was meant to remove. Empty means both
            target counts are 0.
        immutable: Zero-based codon indices to keep exactly, such as the start and the
            stop codon. Empty pins nothing and makes ``immutable_unchanged`` vacuous,
            so pass the positions the goal names rather than relying on the default.
    """

    name: Literal["constraint_check"] = "constraint_check"
    reference: Dna
    targeted_codons: tuple[str, ...] = ()
    immutable: tuple[Annotated[int, Field(strict=True, ge=0)], ...] = ()
    # Raised from 1: the output gained immutable_unchanged and immutable_checked, so a
    # cached result from version 1 is not this node's result. Existing configurations
    # and any filter column naming them have to be recreated.
    version: ClassVar[int] = 2
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = {
        "protein_unchanged": Score,
        "length_unchanged": Score,
        "immutable_unchanged": Score,
        "immutable_checked": Score,
        "targets_remaining": Score,
        "targets_unreachable": Score,
    }
    intents: ClassVar = (
        "check hard constraints of recoded sequences",
        "verify the protein and CDS length are unchanged",
        "check named codon positions are kept exactly, such as the start and stop",
        "count targeted codons still present in-frame",
        "separate constraint violations from soft scores",
        "validate that a recoding preserved the protein",
    )
    when_to_use: ClassVar = (
        "Use after any recoding or mutation step, before comparing soft scores, when "
        "the goal requires the protein to stay unchanged, named codon positions to be "
        "kept, or named codons to be gone."
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

    @model_validator(mode="after")
    def _check_immutable(self) -> Self:
        if len(set(self.immutable)) != len(self.immutable):
            raise ValueError(f"Repeated immutable codon indices: {self.immutable!r}")
        n = len(codons(self.reference.sequence))
        if bad := [i for i in self.immutable if i >= n]:
            raise ValueError(
                f"Immutable codon indices {bad} are past the reference's {n} codons"
            )
        return self
