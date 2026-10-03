from graphlib import TopologicalSorter
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from node_dag import wiring
from node_dag.factory import NodeConfig
from node_dag.types import TYPES, Value
from node_dag.wiring import PortContract


class Step(BaseModel):
    """One node in a DAG and where each of its inputs comes from.

    Args:
        config: The node to run.
        inputs: Maps each input port in ``config.inputs`` to a source. A source is a
            DAG input name, a tool step key, or ``<decision step>.yes`` / ``.no``.
            A step runs only when every source has a value; else it is skipped.
    """

    config: NodeConfig
    inputs: dict[str, str]

    def deps(self) -> set[str]:
        """The DAG inputs and step keys this step reads from."""
        return wiring.deps(self.inputs)

    def contract(self) -> PortContract:
        """The ports and kinds of the node this step runs."""
        return PortContract.of(type(self.config))


class Dag(BaseModel):
    """A DAG of steps. Validation rejects cycles, unknown sources, and type mismatches.

    Args:
        inputs: Maps each DAG input name to its type, e.g. ``{"x": "foo_bar"}``.
        steps: The steps, keyed by name. Keys must not contain ``.`` or repeat an
            input name.
    """

    inputs: dict[str, str]
    steps: dict[str, Step]

    def order(self) -> TopologicalSorter:
        """A sorter over steps and inputs, keyed by name."""
        return wiring.order(self.steps)

    @model_validator(mode="after")
    def _check(self) -> Self:
        wiring.check_wiring(self.inputs, self.steps)
        return self


class DagInput(BaseModel):
    """Input to DagWorkflow.

    Args:
        dag: The steps to run.
        inputs: A value for each name in ``dag.inputs``, of the type it declares.
    """

    dag: Dag
    inputs: dict[str, Value]

    @model_validator(mode="after")
    def _check(self) -> Self:
        want = {k: TYPES[v] for k, v in self.dag.inputs.items()}
        got = {k: type(v) for k, v in self.inputs.items()}
        if want != got:
            raise ValueError(f"DAG wants inputs {want}, got {got}")
        return self


class DagOutput(BaseModel):
    """Result of DagWorkflow.

    Args:
        values: Every value produced, keyed by source: inputs, tool step keys, and
            the ``<decision>.yes`` / ``.no`` branch each decision took.
        skipped: Steps that did not run because a source had no value.
    """

    values: dict[str, Value]
    skipped: list[str]


StepStatus = Literal["pending", "running", "done", "skipped", "failed"]


class DagProgress(BaseModel):
    """How far a DagWorkflow has got. The answer to its ``progress`` query.

    Args:
        dag: The DAG being run.
        steps: The status of each step, keyed by step key.
        values: Every value produced so far, keyed by source, as in DagOutput.
    """

    dag: Dag
    steps: dict[str, StepStatus]
    values: dict[str, Value]
