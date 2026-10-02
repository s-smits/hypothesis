from typing import ClassVar, Literal

from node_dag.nodes.base import BaseToolConfig, Category
from node_dag.types import Baz, FooBar


class ToBazConfig(BaseToolConfig):
    """Turn a FooBar into a Baz labelled ``<prefix><count>``.

    Args:
        prefix: Text put before the count.
    """

    name: Literal["to_baz"] = "to_baz"
    prefix: str = ""
    categories = (Category.CONVERSION,)
    inputs: ClassVar = {"value": FooBar}
    output: ClassVar = Baz
