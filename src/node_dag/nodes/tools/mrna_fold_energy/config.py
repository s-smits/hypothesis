from typing import ClassVar, Literal

from node_dag.nodes.base import BaseScoreConfig, Category
from node_dag.types import NucleicAcid, Score


class MrnaFoldEnergyConfig(BaseScoreConfig):
    """Fold each sequence whole as mRNA and score the structure's free energy.

    Where ``mrna_5prime_mfe`` folds a fixed window at the translation start,
    this folds the entire sequence, so it sees structure anywhere along the
    transcript: structure that can slow elongation or change how long the mRNA
    survives, neither of which an initiation-window score reaches. Folding uses
    ViennaRNA, the same library ``mrna_5prime_mfe`` and ``ostir_expression``
    use. An RNA input folds as itself; a DNA input folds as the mRNA it would
    make. There is no UTR option, because a UTR is a fixed prefix that would
    shift every candidate's energy by about the same amount.

    Folding is cubic in length, so a long transcript is slow: a few kilobases
    takes on the order of a minute, which is why this node allows longer than
    the usual step. Prefer ``mrna_5prime_mfe`` when only the start matters.

    Scores:
        mfe: The minimum free energy of the whole sequence, in kcal/mol. More
            negative means stronger structure.
        ensemble_energy: The free energy of the thermodynamic ensemble, in
            kcal/mol. It is never above the MFE, and the gap between the two
            says how many structures compete: a wide gap means the MFE
            structure is one of many rather than the single answer.
        mfe_per_base: The MFE divided by the number of bases folded. MFE grows
            with length on its own, so compare sequences of different lengths
            on this rather than on ``mfe``. For candidate recodings of one
            protein, which share a length, the two rank identically.
    """

    name: Literal["mrna_fold_energy"] = "mrna_fold_energy"
    categories = (Category.SCORING,)
    inputs: ClassVar = {"sequence": NucleicAcid}
    output: ClassVar = {
        "mfe": Score,
        "ensemble_energy": Score,
        "mfe_per_base": Score,
    }
    # Folding is cubic in length, so a kilobase transcript outlasts a usual step.
    timeout_minutes: ClassVar[int] = 20
    intents: ClassVar = (
        "score whole-transcript mRNA secondary structure",
        "measure full-length folding free energy of an mRNA",
        "compare how structured recoded sequences are overall",
    )
    when_to_use: ClassVar = (
        "Use when structure anywhere in the transcript matters, e.g. for mRNA "
        "stability or elongation, rather than only at the translation start."
    )
    when_not_to_use: ClassVar = (
        "Do not use for the start codon region alone (mrna_5prime_mfe, which "
        "is far cheaper) or for translation initiation rate "
        "(ostir_expression). Avoid it on very long sequences."
    )
