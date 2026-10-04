from typing import ClassVar, Literal

from pydantic import field_validator

from node_dag.dna import check_codon_weights
from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import AminoAcidSequence, Dna


class ProteinToDnaConfig(BaseToolConfig):
    """Back-translate each protein to a coding sequence, choosing codons by weight.

    The inverse of ``dna_to_protein``, and what lets a designed or folded protein
    re-enter the DNA half of a graph: ``protlib_design`` and ``esmfold2_fold``
    produce amino acids, and nothing until now turned those back into DNA to
    score with ``codon_adaptation``, ``ostir_expression`` or ``motif_count``.

    ``most_frequent`` takes the highest-weighted codon for each amino acid, the
    classic back-translation, and ``least_frequent`` takes the lowest.
    ``weighted_sample`` draws each codon with probability proportional to its
    weight, seeded per sequence, so a protein's coding sequence does not depend
    on the rest of the batch. The same table scores a ``codon_adaptation`` run,
    so keep them paired.

    An amino acid whose codons all weigh the same, including all zero or all
    absent from the table, takes the first of its codons in the genetic code's
    own order: with no information there is nothing to choose on, and an
    arbitrary but fixed choice keeps the node deterministic. A ``*`` in the
    protein becomes a stop codon. No stop codon is appended, so a protein
    written without a trailing ``*`` back-translates to a CDS without one.

    Args:
        codon_weights: The usage weight of each codon, e.g. per-thousand
            frequency or relative adaptiveness from a codon usage table. The
            table is part of this config's hash, so its provenance belongs in
            the experiment record.
        strategy: How to choose among an amino acid's codons.
        seed: Seeds ``weighted_sample``. Ignored by the other strategies.
    """

    name: Literal["protein_to_dna"] = "protein_to_dna"
    codon_weights: dict[str, float]
    strategy: Literal["most_frequent", "least_frequent", "weighted_sample"] = (
        "most_frequent"
    )
    seed: int = 0
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"sequence": AminoAcidSequence}
    output: ClassVar = Dna
    intents: ClassVar = (
        "back-translate protein to DNA coding sequence",
        "convert amino acid sequences to codon-optimised DNA",
        "design a gene for a designed or predicted protein",
    )
    when_to_use: ClassVar = (
        "Use to turn amino acid sequences into DNA, so that protein-side nodes "
        "can feed the DNA scores, and to codon-optimise a protein from scratch."
    )
    when_not_to_use: ClassVar = (
        "Do not use to respell DNA that already exists (codon_optimise) or to "
        "translate DNA to protein (dna_to_protein)."
    )

    @field_validator("codon_weights")
    @classmethod
    def _check_weights(cls, v: dict[str, float]) -> dict[str, float]:
        return check_codon_weights(v)
