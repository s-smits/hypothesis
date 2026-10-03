from typing import Annotated

from pydantic import Discriminator

from node_dag.nodes.base import BaseNode, BaseNodeConfig
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.nodes.decisions.at_least.function import AtLeast
from node_dag.nodes.decisions.codons_absent.config import CodonsAbsentConfig
from node_dag.nodes.decisions.codons_absent.function import CodonsAbsent
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_atom_score.function import DnaAtomScore
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.dna_to_protein.function import DnaToProtein
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous
from node_dag.nodes.tools.recode_codons.config import RecodeCodonsConfig
from node_dag.nodes.tools.recode_codons.function import RecodeCodons

NodeConfig = Annotated[
    AtLeastConfig
    | CodonsAbsentConfig
    | DnaAtomScoreConfig
    | DnaToProteinConfig
    | MutateSynonymousConfig
    | RecodeCodonsConfig,
    Discriminator("name"),
]

MAPPING: dict[type[BaseNodeConfig], type[BaseNode]] = {
    AtLeastConfig: AtLeast,
    CodonsAbsentConfig: CodonsAbsent,
    DnaAtomScoreConfig: DnaAtomScore,
    DnaToProteinConfig: DnaToProtein,
    MutateSynonymousConfig: MutateSynonymous,
    RecodeCodonsConfig: RecodeCodons,
}


def build(config: BaseNodeConfig) -> BaseNode:
    """Return the node that ``config`` names."""
    return MAPPING[type(config)](config)
