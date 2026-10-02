from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import FooBar


class AddConfig(BaseToolConfig):
    """Add an integer to a FooBar's count.

    Args:
        amount: The integer to add. Can be negative.
    """

    name: Literal["add"] = "add"
    amount: int
    categories = (Category.ARITHMETIC,)
    inputs: ClassVar = {"value": FooBar}
    output: ClassVar = FooBar
