from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import FooBar


class SumConfig(BaseToolConfig):
    """Add two FooBars' counts, then scale the total.

    Args:
        scale: Multiply the total by this.
    """

    name: Literal["sum"] = "sum"
    scale: int = 1
    categories = (Category.ARITHMETIC,)
    inputs: ClassVar = {"a": FooBar, "b": FooBar}
    output: ClassVar = FooBar
