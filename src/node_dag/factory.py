from typing import Annotated

from pydantic import Discriminator

from node_dag.nodes.base import BaseNode, BaseNodeConfig
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.nodes.filters.at_least.function import AtLeast
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.filters.at_most.function import AtMost
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
from node_dag.nodes.tools.esmfold2_fold.config import Esmfold2FoldConfig
from node_dag.nodes.tools.esmfold2_fold.function import Esmfold2Fold
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.ostir_expression.function import OstirExpression
from node_dag.nodes.tools.recode_targeted.config import RecodeTargetedConfig
from node_dag.nodes.tools.recode_targeted.function import RecodeTargeted
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.nodes.tools.rna_back_transcribe.function import RnaBackTranscribe

NodeConfig = Annotated[
    AtLeastConfig
    | AtMostConfig
    | ConstraintCheckConfig
    | DnaComplementConfig
    | DnaReverseComplementConfig
    | DnaToProteinConfig
    | DnaTranscribeConfig
    | Esmfold2FoldConfig
    | MutateSynonymousConfig
    | OstirExpressionConfig
    | RecodeTargetedConfig
    | RnaBackTranscribeConfig,
    Discriminator("name"),
]

MAPPING: dict[type[BaseNodeConfig], type[BaseNode]] = {
    AtLeastConfig: AtLeast,
    AtMostConfig: AtMost,
    ConstraintCheckConfig: ConstraintCheck,
    DnaComplementConfig: DnaComplement,
    DnaReverseComplementConfig: DnaReverseComplement,
    DnaToProteinConfig: DnaToProtein,
    DnaTranscribeConfig: DnaTranscribe,
    Esmfold2FoldConfig: Esmfold2Fold,
    MutateSynonymousConfig: MutateSynonymous,
    OstirExpressionConfig: OstirExpression,
    RecodeTargetedConfig: RecodeTargeted,
    RnaBackTranscribeConfig: RnaBackTranscribe,
}


def build(config: BaseNodeConfig) -> BaseNode:
    """Return the node that ``config`` names."""
    return MAPPING[type(config)](config)
