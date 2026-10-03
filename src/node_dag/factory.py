from typing import Annotated

from pydantic import Discriminator

from node_dag.nodes.base import BaseNode, BaseNodeConfig
from node_dag.nodes.filters.at_least.config import AtLeastConfig
from node_dag.nodes.filters.at_least.function import AtLeast
from node_dag.nodes.filters.at_most.config import AtMostConfig
from node_dag.nodes.filters.at_most.function import AtMost
from node_dag.nodes.tools.dna_atom_score.config import DnaAtomScoreConfig
from node_dag.nodes.tools.dna_atom_score.function import DnaAtomScore
from node_dag.nodes.tools.dna_to_protein.config import DnaToProteinConfig
from node_dag.nodes.tools.dna_to_protein.function import DnaToProtein
from node_dag.nodes.tools.mutate_synonymous.config import MutateSynonymousConfig
from node_dag.nodes.tools.mutate_synonymous.function import MutateSynonymous

NodeConfig = Annotated[
    AtLeastConfig
    | AtMostConfig
    | DnaAtomScoreConfig
    | DnaToProteinConfig
    | MutateSynonymousConfig,
    Discriminator("name"),
]

MAPPING: dict[type[BaseNodeConfig], type[BaseNode]] = {
    AtLeastConfig: AtLeast,
    AtMostConfig: AtMost,
    DnaAtomScoreConfig: DnaAtomScore,
    DnaToProteinConfig: DnaToProtein,
    MutateSynonymousConfig: MutateSynonymous,
}


def build(config: BaseNodeConfig) -> BaseNode:
    """Return the node that ``config`` names."""
    return MAPPING[type(config)](config)
