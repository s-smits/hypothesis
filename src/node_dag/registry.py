import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from node_dag.factory import NodeConfig
from node_dag.nodes.base import BaseFilterConfig, BaseScoreConfig
from node_dag.storage import write_atomic

logger = logging.getLogger(__name__)

# ``<node name>__<config hash>``, the only shape an id has. A builder picks the id of a
# step's node, so anything else must never be joined into a path.
NODE_ID = re.compile(r"[a-z][a-z0-9_]{0,79}__[0-9a-f]{8}")


class RegisteredNode(BaseModel):
    """A node config made on purpose, with what it is for.

    Its id is ``<node name>__<config hash>``. The same config always has the same id, so
    registering it again finds the first entry.

    Args:
        description: What the node is for, in a sentence.
        config: The node, with its ``config_hash`` set.
    """

    description: str
    config: NodeConfig

    @property
    def id(self) -> str:
        """``<node name>__<config hash>``."""
        return f"{self.config.name}__{self.config.config_hash}"

    def summary(self) -> dict[str, Any]:
        """What a builder needs to wire this node in.

        ``input`` is the port and the kind it takes. ``outputs`` are the sources a step
        of this node makes, with ``<step>`` for its key. A scorer also has
        ``score_columns``: each score name with the full column name it adds. A filter
        has ``filters_on``: the column it reads.
        """
        c = self.config
        contract = c.contract()
        out: dict[str, Any] = {
            "node": self.id,
            "description": self.description,
            "categories": contract["categories"],
            "config": c.model_dump(mode="json"),
            "input": contract["inputs"],
            "outputs": contract["outputs"],
        }
        if isinstance(c, BaseScoreConfig):
            out["score_columns"] = c.columns()
        if isinstance(c, BaseFilterConfig):
            out["filters_on"] = c.column
        return out


class Registry:
    """The nodes made so far, kept as one file each under ``root``.

    Registered nodes last across hypotheses, so a builder can reuse a node that an
    earlier one made.
    """

    def __init__(self, root: Path) -> None:
        """Keep nodes in ``root``, which need not exist yet."""
        self.root = root

    def _path(self, node_id: str) -> Path:
        return self.root / f"{node_id}.json"

    def _load(self, path: Path) -> RegisteredNode | None:
        """The node in ``path``, or None if there is none or it no longer validates."""
        try:
            return RegisteredNode.model_validate_json(path.read_bytes())
        except FileNotFoundError:
            return None
        except ValidationError as e:  # Saved before its config changed shape.
            logger.warning(
                "Ignoring %s: it no longer validates (%d errors)", path, e.error_count()
            )
            return None

    def get(self, node_id: str) -> RegisteredNode | None:
        """The node with this id, or None. Only ``<name>__<hash>`` ids are looked up."""
        if not NODE_ID.fullmatch(node_id):
            return None
        return self._load(self._path(node_id))

    def all(self) -> list[RegisteredNode]:
        """Every registered node that still validates, in id order."""
        return [n for p in sorted(self.root.glob("*.json")) if (n := self._load(p))]

    def score_columns(self) -> set[str]:
        """The full name of every score column that a registered scorer adds."""
        return {
            col
            for n in self.all()
            if isinstance(n.config, BaseScoreConfig)
            for col in n.config.columns().values()
        }

    def register(
        self, config: NodeConfig, description: str
    ) -> tuple[RegisteredNode, bool]:
        """Add a node and return it, and whether it is new.

        A config that is already registered keeps its first description. A filter must
        read a column that a registered scorer adds, so make the scorer first.

        Raises:
            ValueError: If a filter's column is not one that a registered scorer adds.
        """
        if isinstance(config, BaseFilterConfig):
            columns = self.score_columns()
            if config.column not in columns:
                raise ValueError(
                    f"{config.name} filters on {config.column!r}, which no registered "
                    f"node scores. Register the scorer first. Columns: {sorted(columns)}"
                )
        node = RegisteredNode(description=description, config=config)
        if existing := self.get(node.id):
            return existing, False
        write_atomic(self._path(node.id), node.model_dump_json(indent=2).encode())
        return node, True
