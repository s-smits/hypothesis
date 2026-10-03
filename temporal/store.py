"""Where results live on disk.

A leaf module: it imports ``results_root`` and ``write_atomic`` and nothing else from
this package. Both the Temporal workflow and the UI read the layout from here, so
neither has to import the other (nor the pydantic-ai agents) to find a file.

Every path is a function, not a constant: ``results_root()`` reads
``$NODE_DAG_RESULTS`` at call time.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from temporal.dag.activities import results_root, write_atomic

if TYPE_CHECKING:  # Importing node_dag.agent at runtime would pull in pydantic_ai.
    from node_dag.agent import Hypothesis


def hypotheses_dir() -> Path:
    """``$NODE_DAG_RESULTS/hypotheses``: one ``<hypothesis id>.json`` per Hypothesis."""
    return results_root() / "hypotheses"


def requests_dir() -> Path:
    """``$NODE_DAG_RESULTS/requests``: one ``<name>.json`` per requested tool."""
    return results_root() / "requests"


def trajectories_dir() -> Path:
    """``$NODE_DAG_RESULTS/trajectories``: one JSON file per attempt sequence."""
    return results_root() / "trajectories"


def briefs_dir() -> Path:
    """``$NODE_DAG_RESULTS/briefs``: one JSON file per human-facing brief."""
    return results_root() / "briefs"


def save_hypothesis(hyp: "Hypothesis") -> "Hypothesis":
    """Write ``hyp`` to its file and return it."""
    path = hypotheses_dir() / f"{hyp.id}.json"
    write_atomic(path, hyp.model_dump_json(indent=2).encode())
    return hyp


def save_tool_request(name: str, payload: bytes) -> Path:
    """Write ``payload`` to ``requests_dir()/<name>.json`` and return that path."""
    path = requests_dir() / f"{name}.json"
    write_atomic(path, payload)
    return path
