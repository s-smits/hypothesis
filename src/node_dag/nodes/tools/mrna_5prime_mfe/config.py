from typing import ClassVar, Literal

from pydantic import Field, field_validator

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import NucleicAcid, Score


class Mrna5primeMfeConfig(BaseScoreConfig):
    """Fold each sequence's 5' end as mRNA and score the structure's free energy.

    Strong secondary structure around the start codon can keep ribosomes off
    the mRNA, so 5' folding is a standard codon-optimisation constraint. The
    folded region is ``utr`` plus the first ``cds_bases`` of the sequence,
    folded with ViennaRNA's ``RNA.fold`` — the same library ``ostir_expression``
    uses. An RNA input folds as itself; a DNA input folds as the mRNA it would
    make.

    Scores:
        mfe: The minimum free energy of the folded region, in kcal/mol. More
            negative means stronger structure. Filter with ``at_least`` to keep
            5' ends that stay open.

    Args:
        utr: The 5' UTR to put in front of every sequence, upper-case A, C, G,
            T. Folding with no UTR scores the coding start alone.
        cds_bases: How many coding bases after the UTR take part in the fold.
            A shorter sequence folds in full.
    """

    name: Literal["mrna_5prime_mfe"] = "mrna_5prime_mfe"
    utr: str = ""
    cds_bases: int = Field(default=60, ge=1)
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": NucleicAcid}
    output: ClassVar = {"mfe": Score}
    intents: ClassVar = (
        "score 5' mRNA secondary structure free energy",
        "measure folding strength around the start codon",
        "check ribosome accessibility of the mRNA 5' end",
    )
    when_to_use: ClassVar = (
        "Use when the goal asks about mRNA structure near the translation "
        "start, or to keep recoded sequences' 5' ends unstructured."
    )
    when_not_to_use: ClassVar = (
        "Do not use for translation initiation rate (ostir_expression models "
        "that, including Shine-Dalgarno pairing) or protein structure."
    )

    @field_validator("utr")
    @classmethod
    def _check_bases(cls, v: str) -> str:
        if set(v) - set("ACGT"):
            raise ValueError(f"Not a sequence of A, C, G, T: {v!r}")
        return v
