from typing import Annotated

from pydantic import Discriminator

from node_dag.nodes.base import BaseNode, BaseNodeConfig
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.nodes.filters.at_least.function import AtLeast
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.filters.at_most.function import AtMost
from node_dag.nodes.filters.pareto_front.config import ParetoFrontConfig
from node_dag.nodes.filters.pareto_front.function import ParetoFront
from node_dag.nodes.filters.top_k.config import TopKConfig
from node_dag.nodes.filters.top_k.function import TopK
from node_dag.nodes.tools.chain_contacts.config import ChainContactsConfig
from node_dag.nodes.tools.chain_contacts.function import ChainContacts
from node_dag.nodes.tools.codon_adaptation.config import CodonAdaptationConfig
from node_dag.nodes.tools.codon_adaptation.function import CodonAdaptation
from node_dag.nodes.tools.codon_optimise.config import CodonOptimiseConfig
from node_dag.nodes.tools.codon_optimise.function import CodonOptimise
from node_dag.nodes.tools.codon_pair_score.config import CodonPairScoreConfig
from node_dag.nodes.tools.codon_pair_score.function import CodonPairScore
from node_dag.nodes.tools.constraint_check.config import ConstraintCheckConfig
from node_dag.nodes.tools.constraint_check.function import ConstraintCheck
from node_dag.nodes.tools.dna_complement.config import DnaComplementConfig
from node_dag.nodes.tools.dna_complement.function import DnaComplement
from node_dag.nodes.tools.dna_reverse_complement.config import (
    DnaReverseComplementConfig,
)
from node_dag.nodes.tools.dna_reverse_complement.function import DnaReverseComplement
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.dna_to_protein.function import DnaToProtein
from node_dag.nodes.tools.dna_transcribe.config import DnaTranscribeConfig
from node_dag.nodes.tools.dna_transcribe.function import DnaTranscribe
from node_dag.nodes.tools.domesticate.config import DomesticateConfig
from node_dag.nodes.tools.domesticate.function import Domesticate
from node_dag.nodes.tools.esmfold2_fold.config import Esmfold2FoldConfig
from node_dag.nodes.tools.esmfold2_fold.function import Esmfold2Fold
from node_dag.nodes.tools.gc_content.config import GcContentConfig
from node_dag.nodes.tools.gc_content.function import GcContent
from node_dag.nodes.tools.gc_target_recode.config import GcTargetRecodeConfig
from node_dag.nodes.tools.gc_target_recode.function import GcTargetRecode
from node_dag.nodes.tools.motif_count.config import MotifCountConfig
from node_dag.nodes.tools.motif_count.function import MotifCount
from node_dag.nodes.tools.mrna_5prime_mfe.config import Mrna5primeMfeConfig
from node_dag.nodes.tools.mrna_5prime_mfe.function import Mrna5primeMfe
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.ostir_expression.function import OstirExpression
from node_dag.nodes.tools.pdbfixer_fix.config import PdbfixerFixConfig
from node_dag.nodes.tools.pdbfixer_fix.function import PdbfixerFix
from node_dag.nodes.tools.protlib_design.config import ProtlibDesignConfig
from node_dag.nodes.tools.protlib_design.function import ProtlibDesign
from node_dag.nodes.tools.recode_targeted.config import RecodeTargetedConfig
from node_dag.nodes.tools.recode_targeted.function import RecodeTargeted
from node_dag.nodes.tools.resample_synonymous.config import ResampleSynonymousConfig
from node_dag.nodes.tools.resample_synonymous.function import ResampleSynonymous
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.nodes.tools.rna_back_transcribe.function import RnaBackTranscribe

NodeConfig = Annotated[
    AtLeastConfig
    | AtMostConfig
    | ChainContactsConfig
    | CodonAdaptationConfig
    | CodonOptimiseConfig
    | CodonPairScoreConfig
    | ConstraintCheckConfig
    | DnaComplementConfig
    | DnaReverseComplementConfig
    | DnaToProteinConfig
    | DnaTranscribeConfig
    | DomesticateConfig
    | Esmfold2FoldConfig
    | GcContentConfig
    | GcTargetRecodeConfig
    | MotifCountConfig
    | Mrna5primeMfeConfig
    | MutateSynonymousConfig
    | OstirExpressionConfig
    | ProtlibDesignConfig
    | PdbfixerFixConfig
    | RecodeTargetedConfig
    | ResampleSynonymousConfig
    | ParetoFrontConfig
    | RnaBackTranscribeConfig
    | TopKConfig,
    Discriminator("name"),
]

MAPPING: dict[type[BaseNodeConfig], type[BaseNode]] = {
    AtLeastConfig: AtLeast,
    AtMostConfig: AtMost,
    ChainContactsConfig: ChainContacts,
    CodonAdaptationConfig: CodonAdaptation,
    CodonOptimiseConfig: CodonOptimise,
    CodonPairScoreConfig: CodonPairScore,
    ConstraintCheckConfig: ConstraintCheck,
    DnaComplementConfig: DnaComplement,
    DnaReverseComplementConfig: DnaReverseComplement,
    DnaToProteinConfig: DnaToProtein,
    DnaTranscribeConfig: DnaTranscribe,
    DomesticateConfig: Domesticate,
    Esmfold2FoldConfig: Esmfold2Fold,
    GcContentConfig: GcContent,
    GcTargetRecodeConfig: GcTargetRecode,
    MotifCountConfig: MotifCount,
    Mrna5primeMfeConfig: Mrna5primeMfe,
    MutateSynonymousConfig: MutateSynonymous,
    OstirExpressionConfig: OstirExpression,
    ProtlibDesignConfig: ProtlibDesign,
    PdbfixerFixConfig: PdbfixerFix,
    RecodeTargetedConfig: RecodeTargeted,
    ResampleSynonymousConfig: ResampleSynonymous,
    ParetoFrontConfig: ParetoFront,
    RnaBackTranscribeConfig: RnaBackTranscribe,
    TopKConfig: TopK,
}


def build(config: BaseNodeConfig) -> BaseNode:
    """Return the node that ``config`` names."""
    return MAPPING[type(config)](config)
