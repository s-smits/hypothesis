from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class Category(StrEnum):
    """What a node does. Used for discovery and grouping."""

    ARITHMETIC = "arithmetic"
    CONVERSION = "conversion"
    FILTER = "filter"
    SCORING = "scoring"
    GENERATION = "generation"


def _kind(t: type[BaseModel]) -> str:
    return t.model_fields["kind"].default


def _add_contract(schema: dict[str, Any], cls: type["BaseNodeConfig"]) -> None:
    # A JSON schema leaves ClassVars out. Put them back, so an agent can wire ports.
    schema["x-node"] = cls.contract()


class BaseNodeConfig(BaseModel):
    """Base for every node config.

    A subclass sets a unique ``name`` literal, ``categories``, ``inputs`` (the port
    names and types that the node's ``run`` takes as keyword arguments) and
    ``example``.

    ``example`` is required because a ``ToolRequest`` demands a worked example for a
    node that does *not* exist yet. Without one here the agent would reason better
    about hypothetical tools than about the real ones it can actually use.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_extra=_add_contract
    )
    name: str
    categories: ClassVar[tuple[Category, ...]] = ()
    inputs: ClassVar[dict[str, type[BaseModel]]] = {}
    # One concrete input, the config that was used, and the output it gives. Rides in
    # ``contract()`` -> ``x-node``, so it reaches the agent through ``describe_node``.
    example: ClassVar[str] = ""
    # Discovery metadata. ``intents`` are short phrases naming the jobs this node does,
    # in the words someone would state a goal in; the two ``when_`` strings say when it
    # is and is not the right choice. All three ride in ``contract()``, so search_nodes
    # can rank on them and describe_node shows them.
    intents: ClassVar[tuple[str, ...]] = ()
    when_to_use: ClassVar[str] = ""
    when_not_to_use: ClassVar[str] = ""
    # Part of the cache key. Raise it when a change to run() changes its results.
    version: ClassVar[int] = 1

    @classmethod
    def outputs(cls) -> dict[str, type[BaseModel]]:
        """The sources a step of this node produces, with ``<step>`` for its key."""
        raise NotImplementedError

    @classmethod
    def contract(cls) -> dict[str, Any]:
        """Categories, a worked example, ports and outputs, and when to pick this node."""
        return {
            "categories": [c.value for c in cls.categories],
            "example": cls.example,
            "inputs": {port: _kind(t) for port, t in cls.inputs.items()},
            "outputs": {src: _kind(t) for src, t in cls.outputs().items()},
            "intents": list(cls.intents),
            "when_to_use": cls.when_to_use,
            "when_not_to_use": cls.when_not_to_use,
        }


class BaseToolConfig(BaseNodeConfig):
    """A tool config. ``output`` is the type that ``run`` returns."""

    output: ClassVar[type[BaseModel]]

    @classmethod
    def outputs(cls) -> dict[str, type[BaseModel]]:
        """A tool step produces ``output`` under its own key."""
        return {"<step>": cls.output}


class BaseDecisionConfig(BaseNodeConfig):
    """A decision config. ``forwards`` names the input port the decision passes on.

    A decision emits that input on ``<step>.yes`` or ``<step>.no``, never both.
    """

    forwards: ClassVar[str]

    @classmethod
    def outputs(cls) -> dict[str, type[BaseModel]]:
        """A decision step forwards one input on ``.yes`` or ``.no``."""
        t = cls.inputs[cls.forwards]
        return {"<step>.yes": t, "<step>.no": t}


class BaseNode[C: BaseNodeConfig](ABC):
    """Base for every node. Takes its config at construction and its inputs in ``run``."""

    def __init__(self, config: C) -> None:
        """Keep the config."""
        self.config = config

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Run on keyword inputs named as in ``config.inputs``."""
