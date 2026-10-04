from typing import Annotated, ClassVar, Literal

from pydantic import Discriminator

from node_dag.nodes.base import BaseFilterConfig, Category
from node_dag.types import AminoAcidSequence, Dna, Entity, Rna

# A sequence entity: the other kinds carry more than a sequence, so their schema is long
# and nothing needs to be compared against one yet.
Sequence = Annotated[Dna | Rna | AminoAcidSequence, Discriminator("kind")]


class BeatsReferenceConfig(BaseFilterConfig):
    """Keep the entities that score strictly better than a reference entity does.

    This is "keep the ones that score higher than the first sequence" written as a
    measurement. The reference's score is read from the run, not typed in, so the bound
    cannot drift from the baseline and the reference itself, which only ties, goes to the
    no branch. Non-finite scores are never kept.

    Score the reference with the same node and settings as the entities it is compared
    with, for example by giving the scoring node the DAG input as well, in a step of its
    own. ``scored_in`` names that step and runs before this filter.

    Args:
        column: The score column to compare, ``<node name>__<config hash>__<score name>``.
        reference: The entity to beat, for example the first input sequence.
        scored_in: The step or DAG input whose table holds the reference's score in
            ``column``.
        higher: Keep the entities scoring above the reference. Set it False to keep the
            ones scoring below it.
    """

    name: Literal["beats_reference"] = "beats_reference"
    reference: Sequence
    scored_in: str
    higher: bool = True
    categories = (Category.FILTER,)
    inputs: ClassVar = {"items": Entity}
    intents: ClassVar = (
        "keep entities that score higher than the original or first sequence",
        "keep only improvements over a baseline entity's own score",
        "filter against a reference entity's measured score, not a typed threshold",
    )
    when_to_use: ClassVar = (
        "Use when the goal says to keep what scores higher (or lower) than a named "
        "entity such as the first input: the baseline is measured, so no threshold has "
        "to be copied from an earlier round."
    )
    when_not_to_use: ClassVar = (
        "Do not use for an absolute cutoff that the goal states as a number; at_least "
        "and at_most do that. It needs the reference scored with the same node first."
    )

    def reads_reference(self) -> tuple[str, str] | None:
        """The step that scored the reference, and the reference's id."""
        return self.scored_in, self.reference.id
