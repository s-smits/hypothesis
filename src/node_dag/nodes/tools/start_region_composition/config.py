from typing import ClassVar, Literal

from pydantic import Field

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Dna, Score


class StartRegionCompositionConfig(BaseToolConfig):
    """Score the first few codons by how well they start translation in E. coli.

    The base composition of the first six codons turns out to govern expression
    almost on its own, and not through GC content. Synonymous changes confined to
    those eighteen bases have been reported to move protein output by up to ten-fold:
    adenosine there helps the 70S initiation complex form, while guanosine leads to
    the mRNA being degraded before it is productively translated. See Nucleic Acids
    Research 53(22), gkaf1262 (2025).

    The score is the A fraction minus the G fraction over that region, so it runs from
    -1 to 1 and higher is better. Select on it with ``at_least``. A threshold of 0 asks
    only that A is not outnumbered by G; around 0.2 is a real preference for an A-rich
    start.

    This is the cheapest check of the three that bear on expression, and the most
    specific. It is a rule about composition, so it cannot see folding: use
    ``ostir_expression`` for that, which measures initiation directly but costs a
    ViennaRNA fold per sequence.

    Recoding reaches this region like any other, which is the point. A swap in the
    second codon is as synonymous as one in the hundredth and matters far more.

    Args:
        codons: How many codons from the start to score. Six is the published window;
            effects have been reported out to seven.
    """

    name: Literal["start_region_composition"] = "start_region_composition"
    codons: int = Field(default=6, ge=1)
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": Dna}
    output: ClassVar = Score
    example: ClassVar = (
        "sequence=ATGAAAGCAAAAGCAAAA -> score 0.5: over the first six codons there are "
        "nine A and no G. sequence=ATGGGCGGCGGCGGCGGC -> score -0.61, a G-rich start. "
        "Filter with at_least(threshold=0) to reject starts where G outnumbers A."
    )
    intents: ClassVar = (
        "check that a recoded gene still starts translating well",
        "score the first codons of a coding sequence",
        "avoid a G-rich start that gets the mRNA degraded",
        "keep expression after a synonymous change near the start codon",
    )
    when_to_use: ClassVar = (
        "Use whenever a recoding may touch the first six codons and the goal cares "
        "about expression at all. It is the single largest lever on how much protein a "
        "coding sequence makes in E. coli, and it is invisible to every check about "
        "codon content or protein identity, which a swap here passes perfectly."
    )
    when_not_to_use: ClassVar = (
        "Do not use it on a sequence that does not begin at its start codon, since the "
        "window would be meaningless. Do not use it as a general expression measure "
        "across the whole gene: it reads only the start, and ostir_expression measures "
        "initiation itself rather than a composition rule for it."
    )
    # Part of the node result cache key, with the config and the inputs. Bump it after
    # any change to run(), or the cached result of the old code is served forever.
    version: ClassVar[int] = 1
