from typing import Annotated

from pydantic import Discriminator

from node_dag.nodes.base import BaseNode, BaseNodeConfig
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.nodes.decisions.at_least.function import AtLeast
from node_dag.nodes.decisions.at_most.config import AtMostConfig
from node_dag.nodes.decisions.at_most.function import AtMost
from node_dag.nodes.decisions.codons_absent.config import CodonsAbsentConfig
from node_dag.nodes.decisions.codons_absent.function import CodonsAbsent
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_atom_score.function import DnaAtomScore
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
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.ostir_expression.config import OstirExpressionConfig
from node_dag.nodes.tools.ostir_expression.function import OstirExpression
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons
from node_dag.nodes.tools.rna_back_transcribe.config import RnaBackTranscribeConfig
from node_dag.nodes.tools.rna_back_transcribe.function import RnaBackTranscribe

NodeConfig = Annotated[
    AtLeastConfig
    | AtMostConfig
    | CodonsAbsentConfig
    | DnaAtomScoreConfig
    | DnaComplementConfig
    | DnaReverseComplementConfig
    | DnaToProteinConfig
    | DnaTranscribeConfig
    | MutateSynonymousConfig
    | OstirExpressionConfig
    | RecodeCodonsConfig
    | RnaBackTranscribeConfig,
    Discriminator("name"),
]

MAPPING: dict[type[BaseNodeConfig], type[BaseNode]] = {
    AtLeastConfig: AtLeast,
    AtMostConfig: AtMost,
    CodonsAbsentConfig: CodonsAbsent,
    DnaAtomScoreConfig: DnaAtomScore,
    DnaComplementConfig: DnaComplement,
    DnaReverseComplementConfig: DnaReverseComplement,
    DnaToProteinConfig: DnaToProtein,
    DnaTranscribeConfig: DnaTranscribe,
    MutateSynonymousConfig: MutateSynonymous,
    OstirExpressionConfig: OstirExpression,
    RecodeCodonsConfig: RecodeCodons,
    RnaBackTranscribeConfig: RnaBackTranscribe,
}


def build(config: BaseNodeConfig) -> BaseNode:
    """Return the node that ``config`` names."""
    return MAPPING[type(config)](config)
