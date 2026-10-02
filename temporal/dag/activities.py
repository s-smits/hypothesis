import hashlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, TypeAdapter
from temporalio import activity

from node_dag import factory
from node_dag.dag import DagProgress
from node_dag.factory import NodeConfig
from node_dag.types import Value


def results_root() -> Path:
    """``$NODE_DAG_RESULTS``, or ``results`` if it is not set."""
    # ponytail: a local directory, so only workers on this machine share it. Move
    # to S3 when workers run on more than one machine.
    return Path(os.environ.get("NODE_DAG_RESULTS", "results"))


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path``, making its directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write then rename, so a reader never sees half a file.
    tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class RunNodeInput(BaseModel):
    """Input to the node activities.

    Args:
        config: The node to run.
        inputs: The values for the node's input ports.
    """

    config: NodeConfig
    inputs: dict[str, Value]

    def cache_path(self) -> Path:
        """``$NODE_DAG_RESULTS/nodes/<node name>/<hash>.json``. The root defaults to ``results``.

        The hash covers the config, the input values and the config's ``version``.
        """
        key = {**self.model_dump(mode="json"), "version": self.config.version}
        digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
        return results_root() / "nodes" / self.config.name / f"{digest}.json"


def _cached[T](inp: RunNodeInput, adapter: TypeAdapter[T], run: Callable[[], T]) -> T:
    path = inp.cache_path()
    if path.exists():
        return adapter.validate_json(path.read_bytes())
    out = run()
    write_atomic(path, adapter.dump_json(out))
    return out


_VALUE: TypeAdapter[Value] = TypeAdapter(Value)
_BOOL = TypeAdapter(bool)


@activity.defn
def run_tool(inp: RunNodeInput) -> Value:
    """Run one tool node through the factory, or load its cached result."""
    return _cached(inp, _VALUE, lambda: factory.build(inp.config).run(**inp.inputs))


@activity.defn
def run_decision(inp: RunNodeInput) -> bool:
    """Run one decision node through the factory, or load its cached result."""
    return _cached(inp, _BOOL, lambda: factory.build(inp.config).run(**inp.inputs))


class SaveWorkflowInput(BaseModel):
    """Input to save_workflow.

    Args:
        workflow_id: The ID of the DagWorkflow run.
        progress: The run's DAG, the status of each step and every value it produced.
    """

    workflow_id: str
    progress: DagProgress

    def path(self) -> Path:
        """``$NODE_DAG_RESULTS/workflows/<workflow id>.json``."""
        return self.path_for(self.workflow_id)

    @staticmethod
    def path_for(workflow_id: str) -> Path:
        """Where save_workflow writes the run ``workflow_id``."""
        return results_root() / "workflows" / f"{workflow_id}.json"


@activity.defn
async def save_workflow(inp: SaveWorkflowInput) -> None:
    """Write a finished or failed DagWorkflow run to disk."""
    write_atomic(inp.path(), inp.progress.model_dump_json(indent=2).encode())
