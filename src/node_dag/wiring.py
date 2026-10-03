from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from node_dag.nodes.base import (
    BaseDecisionConfig,
    BaseNodeConfig,
    BaseToolConfig,
    _kind,
)
from node_dag.types import TYPES


class PortContract(BaseModel):
    """What one step of a node takes and produces, as kind names.

    The same shape for a node that exists and for one only requested, so a plan of
    hypothetical nodes typechecks exactly like a Dag.

    Args:
        inputs: Maps each input port to the kind name it takes.
        output: The kind a tool returns under its own step key. ``None`` for decisions.
        forwards: The input port a decision passes on. ``None`` for tools.
    """

    inputs: dict[str, str]
    output: str | None = None
    forwards: str | None = None

    @classmethod
    def of(cls, config: type[BaseNodeConfig]) -> "PortContract":
        """The contract a node config declares, with its types as kind names."""
        inputs = {port: _kind(t) for port, t in config.inputs.items()}
        if issubclass(config, BaseToolConfig):
            return cls(inputs=inputs, output=_kind(config.output))
        if issubclass(config, BaseDecisionConfig):
            return cls(inputs=inputs, forwards=config.forwards)
        raise TypeError(f"{config.__name__} is neither a tool nor a decision")

    def sources(self, key: str) -> dict[str, str]:
        """``{key: output}`` for a tool, ``{key.yes, key.no}`` for a decision."""
        if self.output is not None:
            return {key: self.output}
        if self.forwards is not None:
            kind = self.inputs[self.forwards]
            return {f"{key}.yes": kind, f"{key}.no": kind}
        raise ValueError("A contract must set either 'output' or 'forwards'")


@runtime_checkable
class Wired(Protocol):
    """One wired step: where its inputs come from, and what its node takes and gives."""

    inputs: dict[str, str]

    def contract(self) -> PortContract:
        """The ports and kinds of the node this step runs."""
        ...


def deps(inputs: Mapping[str, str]) -> set[str]:
    """The DAG inputs and step keys a step's sources read from."""
    return {src.split(".")[0] for src in inputs.values()}


def order(steps: Mapping[str, Wired]) -> TopologicalSorter:
    """A sorter over steps and inputs, keyed by name."""
    return TopologicalSorter({k: deps(s.inputs) for k, s in steps.items()})


def check_wiring(
    inputs: Mapping[str, str], steps: Mapping[str, Wired]
) -> dict[str, str]:
    """Return the kind each source gives, or raise ValueError on any wiring fault.

    Args:
        inputs: Maps each DAG input name to its kind name.
        steps: The wired steps, keyed by name.
    """
    if bad := [k for k in steps if "." in k or k in inputs]:
        raise ValueError(f"Step keys must not contain '.' or repeat an input: {bad}")
    if bad := {k: v for k, v in inputs.items() if v not in TYPES}:
        raise ValueError(f"Unknown input types {bad}; known: {sorted(TYPES)}")
    # The kind each source produces, filled in dependency order.
    types = dict(inputs)
    try:
        ordered = list(order(steps).static_order())
    except CycleError as e:
        raise ValueError(f"Cycle: {' -> '.join(e.args[1])}") from e
    for key in ordered:
        if key in inputs:
            continue
        if key not in steps:
            raise ValueError(f"Unknown source {key!r}")
        step = steps[key]
        contract = step.contract()
        if step.inputs.keys() != contract.inputs.keys():
            raise ValueError(
                f"Step {key!r} ports {sorted(step.inputs)} != node ports "
                f"{sorted(contract.inputs)}"
            )
        for port, src in step.inputs.items():
            if src not in types:
                raise ValueError(
                    f"Step {key!r} port {port!r}: unknown source {src!r}. "
                    "Read a decision's output as '<step>.yes' or '<step>.no'."
                )
            if types[src] != contract.inputs[port]:
                raise ValueError(
                    f"Step {key!r} port {port!r} takes {contract.inputs[port]}"
                    f", but {src!r} gives {types[src]}"
                )
        types.update(contract.sources(key))
    return types
