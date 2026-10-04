from graphlib import CycleError, TopologicalSorter
from typing import Literal, Self

from pydantic import BaseModel, model_validator

from node_dag.factory import NodeConfig
from node_dag.nodes.base import BaseFilterConfig, BaseScoreConfig, BaseToolConfig
from node_dag.types import TYPES, Entity, Table, Value


class Step(BaseModel):
    """One node in a DAG and where its input comes from.

    Args:
        config: The node to run.
        inputs: Maps each of the node's input ports to a source. A source is a DAG
            input name, a tool or scoring step key, or ``<filter step>.yes`` /
            ``.no``. The node runs once, on every entity each source holds.
    """

    config: NodeConfig
    inputs: dict[str, str]

    def deps(self) -> set[str]:
        """The DAG inputs and step keys this step reads from."""
        deps = {src.split(".")[0] for src in self.inputs.values()}
        if isinstance(self.config, BaseFilterConfig) and (
            ref := self.config.reads_reference()
        ):
            deps.add(ref[0].split(".")[0])
        return deps


class Dag(BaseModel):
    """A DAG of steps. Validation rejects cycles, unknown sources, type mismatches and
    filters on a score column that nothing upstream makes.

    Args:
        inputs: Maps each DAG input name to the kind of the entities in its list, e.g.
            ``{"seqs": "dna"}``.
        steps: The steps, keyed by name. Keys must not contain ``.`` or repeat an
            input name.
    """

    inputs: dict[str, str]
    steps: dict[str, Step]

    def order(self) -> TopologicalSorter:
        """A sorter over steps and inputs, keyed by name."""
        return TopologicalSorter({k: s.deps() for k, s in self.steps.items()})

    def inputs_read(self, source: str) -> set[str] | None:
        """The DAG inputs whose entities can reach ``source`` through scores and filters.

        None if a tool is in the way, since a tool can make any entity.
        """
        name = source.split(".")[0]
        if name in self.inputs:
            return {name}
        step = self.steps[name]
        if isinstance(step.config, BaseToolConfig):
            return None
        (upstream,) = step.inputs.values()
        return self.inputs_read(upstream)

    def _unknown_source(self, name: str) -> str:
        """The error for a source nothing defines. A ``scored_in`` is named as one."""
        for key, step in self.steps.items():
            cfg = step.config
            if (
                isinstance(cfg, BaseFilterConfig)
                and (ref := cfg.reads_reference())
                and ref[0].split(".")[0] == name
            ):
                return (
                    f"Unknown source {name!r}. Step {key!r} ({cfg.name}__{cfg.config_hash}) "
                    f"reads it from its scored_in field, but this plan has no step or "
                    f"input of that name. Set scored_in to the step that scores the "
                    f"reference with the same node as the entities."
                )
        return f"Unknown source {name!r}"

    @model_validator(mode="after")
    def _check(self) -> Self:
        if bad := [k for k in self.steps if "." in k or k in self.inputs]:
            raise ValueError(
                f"Step keys must not contain '.' or repeat an input: {bad}"
            )
        if bad := {k: v for k, v in self.inputs.items() if v not in TYPES}:
            raise ValueError(f"Unknown input types {bad}; known: {sorted(TYPES)}")
        # The entity type and the score columns each source holds, in dependency order.
        types: dict[str, type[Entity]] = {k: TYPES[v] for k, v in self.inputs.items()}
        columns: dict[str, set[str]] = {k: set() for k in self.inputs}
        try:
            order = list(self.order().static_order())
        except CycleError as e:
            raise ValueError(f"Cycle: {' -> '.join(e.args[1])}") from e
        for key in order:
            if key in self.inputs:
                continue
            if key not in self.steps:
                raise ValueError(self._unknown_source(key))
            step, config = self.steps[key], self.steps[key].config
            # Every required port has to be wired, and nothing the node has no port
            # for, but an optional port may simply be left out.
            wired = step.inputs.keys()
            if not config.required() <= wired <= config.inputs.keys():
                optional = (
                    f" ({sorted(config.optional_inputs)} may be left out)"
                    if config.optional_inputs
                    else ""
                )
                raise ValueError(
                    f"Step {key!r} ports {sorted(step.inputs)} != {config.name} ports "
                    f"{sorted(config.inputs)}{optional}"
                )
            for port, src in step.inputs.items():
                if src not in types:
                    raise ValueError(
                        f"Step {key!r} port {port!r}: unknown source {src!r}. "
                        "Read a filter's output as '<step>.yes' or '<step>.no'."
                    )
                if not issubclass(types[src], config.inputs[port]):
                    raise ValueError(  # noqa: TRY004  A validator must raise ValueError.
                        f"Step {key!r} port {port!r} takes "
                        f"{config.inputs[port].__name__}, but {src!r} gives "
                        f"{types[src].__name__}"
                    )
            if isinstance(config, BaseToolConfig):
                # New entities, so none of the old scores apply.
                types[key], columns[key] = config.output, set()
                continue
            # A score or filter has one port, and passes on what comes through it.
            (src,) = step.inputs.values()
            if isinstance(config, BaseScoreConfig):
                types[key] = types[src]
                columns[key] = columns[src] | set(config.columns().values())
            elif isinstance(config, BaseFilterConfig):
                # Every column the filter reads has to reach it, not just the first,
                # or a multi-objective filter would be handed a column that is absent.
                if missing := [
                    c for c in config.score_columns() if c not in columns[src]
                ]:
                    raise ValueError(
                        f"Step {key!r} filters on {missing[0]!r}, but {src!r} has "
                        f"score columns {sorted(columns[src])}"
                    )
                ref = config.reads_reference()
                if ref and config.column not in (held := columns.get(ref[0], set())):
                    raise ValueError(
                        f"Step {key!r} compares with a reference scored in "
                        f"{ref[0]!r}, which has score columns {sorted(held)} but not "
                        f"{config.column!r}"
                    )
                for branch in ("yes", "no"):
                    types[f"{key}.{branch}"] = types[src]
                    columns[f"{key}.{branch}"] = columns[src]
        return self


class DagInput(BaseModel):
    """Input to DagWorkflow.

    Args:
        dag: The steps to run.
        inputs: A list of entities for each name in ``dag.inputs``, of the kind it declares.
    """

    dag: Dag
    inputs: dict[str, list[Value]]

    @model_validator(mode="after")
    def _check(self) -> Self:
        want = {k: TYPES[v] for k, v in self.dag.inputs.items()}
        got = {k: {type(i) for i in v} for k, v in self.inputs.items()}
        if want.keys() != got.keys() or any(got[k] - {t} for k, t in want.items()):
            raise ValueError(f"DAG wants inputs {want}, got {got}")
        return self


class DagOutput(BaseModel):
    """Result of DagWorkflow.

    Args:
        values: The table each source ended with, keyed by source: inputs, tool and
            scoring step keys, and ``<filter>.yes`` / ``.no``. A table's ``scores``
            maps each column, ``<node name>__<config hash>__<score name>``, to the
            score of every entity id.
        skipped: Steps that did not run because no entities reached them.
    """

    values: dict[str, Table]
    skipped: list[str]


StepStatus = Literal["pending", "running", "done", "skipped", "failed"]


class DagProgress(BaseModel):
    """How far a DagWorkflow has got. The answer to its ``progress`` query.

    Args:
        dag: The DAG being run.
        steps: The status of each step, keyed by step key.
        values: Every table produced so far, keyed by source, as in DagOutput.
    """

    dag: Dag
    steps: dict[str, StepStatus]
    values: dict[str, Table]
