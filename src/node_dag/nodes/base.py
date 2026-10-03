import hashlib
import json
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from node_dag.types import Entity, Score


class Category(StrEnum):
    """What a node does. Used for discovery and grouping."""

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

    A node runs once on the whole list of entities that flows into its one input port.
    A subclass sets a unique ``name`` literal, ``categories``, and ``inputs``: the port
    name and entity type that the node's ``run`` takes, as a list, by keyword.

    ``config_hash`` is a hash of the name, version and every other field. It is set when
    the config is made, so it says what the node does. A config that arrives with a
    wrong hash is rejected.
    """

    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_extra=_add_contract
    )
    name: str
    config_hash: str = Field(
        default="", description="Set from the other fields. Leave it out."
    )
    categories: ClassVar[tuple[Category, ...]] = ()
    inputs: ClassVar[dict[str, type[Entity]]] = {}
    intents: ClassVar[tuple[str, ...]] = ()
    when_to_use: ClassVar[str] = ""
    when_not_to_use: ClassVar[str] = ""
    # Part of the cache key and the config hash. Raise it when a change to run()
    # changes its results.
    version: ClassVar[int] = 1

    @model_validator(mode="after")
    def _set_hash(self) -> "BaseNodeConfig":
        fields = self.model_dump(
            mode="json", exclude={"config_hash"}, context={"hashing": True}
        )
        key = json.dumps({"version": self.version, **fields}, sort_keys=True)
        digest = hashlib.sha256(key.encode()).hexdigest()[:8]
        if self.config_hash not in ("", digest):
            raise ValueError(
                f"config_hash {self.config_hash!r} is not {digest!r}, the hash of "
                "these fields. Leave config_hash out."
            )
        object.__setattr__(self, "config_hash", digest)  # The model is frozen.
        return self

    @classmethod
    def outputs(cls) -> dict[str, type[Entity]]:
        """The sources a step of this node produces, with ``<step>`` for its key."""
        raise NotImplementedError

    @classmethod
    def contract(cls) -> dict[str, Any]:
        """Categories, input ports, outputs, intents and usage guidelines."""
        return {
            "categories": [c.value for c in cls.categories],
            "inputs": {port: _kind(t) for port, t in cls.inputs.items()},
            "outputs": {src: _kind(t) for src, t in cls.outputs().items()},
            "intents": list(cls.intents),
            "when_to_use": cls.when_to_use,
            "when_not_to_use": cls.when_not_to_use,
        }

    @classmethod
    def port(cls) -> tuple[str, type[Entity]]:
        """The one input port and its entity type."""
        ((port, t),) = cls.inputs.items()
        return port, t


class BaseToolConfig(BaseNodeConfig):
    """A tool config. ``output`` is the entity type of the list that ``run`` returns.

    ``run`` returns one output entity per input, or any number. The step's table has
    only these entities and no scores, since they are new entities.
    """

    output: ClassVar[type[Entity]]

    @classmethod
    def outputs(cls) -> dict[str, type[Entity]]:
        """A tool step produces ``output`` under its own key."""
        return {"<step>": cls.output}


class BaseScoreConfig(BaseNodeConfig):
    """A scoring config. ``output`` maps each score name to ``Score``.

    ``run`` returns, for each input, a dict with a ``Score`` under each score name. The
    entities pass through, and each score is added to the table as a column named
    ``<name>__<config_hash>__<score name>``.
    """

    output: ClassVar[dict[str, type[Score]]]

    @classmethod
    def outputs(cls) -> dict[str, type[Entity]]:
        """A scoring step passes its input entities on under its own key."""
        return {"<step>": cls.port()[1]}

    @classmethod
    def contract(cls) -> dict[str, Any]:
        """The base contract, and ``scores``: each score name with its type."""
        return {
            **super().contract(),
            "scores": {n: _kind(t) for n, t in cls.output.items()},
        }

    def columns(self) -> dict[str, str]:
        """Maps each score name to its table column."""
        return {s: f"{self.name}__{self.config_hash}__{s}" for s in self.output}


class BaseFilterConfig(BaseNodeConfig):
    """A filter config. ``column`` names the score column to filter on.

    ``run`` gets the entities and that column's ``values``, aligned, and returns a bool
    for each: True keeps it on ``<step>.yes``, False sends it to ``<step>.no``.
    """

    column: str

    @classmethod
    def outputs(cls) -> dict[str, type[Entity]]:
        """A filter step passes its entities on, split into ``.yes`` and ``.no``."""
        t = cls.port()[1]
        return {"<step>.yes": t, "<step>.no": t}


class BaseNode[C: BaseNodeConfig](ABC):
    """Base for every node. Takes its config at construction and its inputs in ``run``."""

    def __init__(self, config: C) -> None:
        """Keep the config."""
        self.config = config

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        """Run on keyword inputs named as in ``config.inputs``, each a list."""
