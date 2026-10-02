from typing import Annotated

from pydantic import Discriminator

from node_dag.nodes.base import BaseNode, BaseNodeConfig
from node_dag.nodes.decisions.at_least.config import AtLeastConfig
from node_dag.nodes.decisions.at_least.function import AtLeast
from node_dag.nodes.tools.add.config import AddConfig
from node_dag.nodes.tools.add.function import Add
from node_dag.nodes.tools.sum.config import SumConfig
from node_dag.nodes.tools.sum.function import Sum
from node_dag.nodes.tools.to_baz.config import ToBazConfig
from node_dag.nodes.tools.to_baz.function import ToBaz

NodeConfig = Annotated[
    AddConfig | SumConfig | ToBazConfig | AtLeastConfig, Discriminator("name")
]

MAPPING: dict[type[BaseNodeConfig], type[BaseNode]] = {
    AddConfig: Add,
    SumConfig: Sum,
    ToBazConfig: ToBaz,
    AtLeastConfig: AtLeast,
}


def build(config: BaseNodeConfig) -> BaseNode:
    """Return the node that ``config`` names."""
    return MAPPING[type(config)](config)
