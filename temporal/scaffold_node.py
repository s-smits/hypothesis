"""Turn a saved ToolRequest into the node package a person still has to finish.

A request is the node's whole contract, and the node layout is regular, so everything
but the body of ``run`` can be written for you. ``--bump`` raises an existing node's
``version``: node results are cached on config, inputs and version, so a fix to ``run``
without a bump keeps serving the old results.
"""

import json
import re
import textwrap
from pathlib import Path

import click

import node_dag.nodes
from node_dag.plan import ConfigField, ToolRequest
from node_dag.types import TYPES
from temporal.dag.activities import results_subdir

WIDTH = 88
ANNOTATIONS = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "list[str]": "tuple[str, ...]",  # BaseNodeConfig is frozen, so a list is a tuple.
}
BASES = {
    "tool": "BaseToolConfig",
    "score": "BaseScoreConfig",
    "filter": "BaseFilterConfig",
}
CATEGORIES = {"tool": "GENERATION", "score": "SCORING", "filter": "FILTER"}
VERSION = re.compile(
    r"^(?P<indent>[ \t]*)version: ClassVar\[int\] = (?P<n>\d+)$", re.MULTILINE
)
NAME = re.compile(r"^(?P<indent>[ \t]*)name: Literal\[.*$", re.MULTILINE)
BUMP_NOTE = (
    "Part of the cache key and the config hash. Raise it when a change to run() changes"
    " its results: `python -m temporal.scaffold_node {name} --bump`."
)


def camel(name: str) -> str:
    """``gc_count`` as ``GcCount``."""
    return "".join(p.title() for p in name.split("_"))


def folder(req: ToolRequest) -> str:
    """``filters`` for a filter; tools and scorers both live under ``tools``."""
    return "filters" if req.node == "filter" else "tools"


def wrap(text: str, indent: str, first: str = "", hanging: str = "") -> list[str]:
    """``text`` wrapped to WIDTH at ``indent``: the first line after ``first``, the rest
    after ``hanging``."""
    return textwrap.wrap(
        text, WIDTH, initial_indent=indent + first, subsequent_indent=indent + hanging
    )


def docstring(
    indent: str, summary: str, sections: dict[str, list[tuple[str, str]]]
) -> list[str]:
    """A Google-style docstring: the summary, then each non-empty section."""
    lines = wrap(summary, indent, '"""')
    for title, entries in sections.items():
        if entries:
            lines += ["", f"{indent}{title}:"]
            for name, text in entries:
                lines += wrap(f"{name}: {text}", indent, "    ", "        ")
    if len(lines) == 1 and len(lines[0]) + 3 <= WIDTH:
        return [lines[0] + '"""']
    return [*lines, f'{indent}"""']


def outputs(req: ToolRequest) -> tuple[str | None, list[str]]:
    """A tool's output class name, and a scorer's score names."""
    made = TYPES[req.output].__name__ if isinstance(req.output, str) else None
    return made, req.output if isinstance(req.output, list) else []


def field_line(f: ConfigField) -> str:
    """One config field, with its default when it is optional."""
    line = f"    {f.name}: {ANNOTATIONS[f.type]}"
    if f.required:
        return line
    if f.default is None:
        return f"{line} | None = None"
    default = tuple(f.default) if isinstance(f.default, list) else f.default
    return f"{line} = {default!r}"


def config_source(req: ToolRequest) -> str:
    """The ``config.py`` for ``req``: its fields, its port, its output and a version."""
    kind, (made, scored) = TYPES[req.kind].__name__, outputs(req)
    names = {kind, *([made] if made else []), *(["Score"] if scored else [])}
    fields = [(f.name, f.description) for f in req.config_fields]
    if req.node == "filter":
        fields.insert(0, ("column", "The score column to filter on."))
    scores = [(s, "TODO: what it measures.") for s in scored]
    lines = [
        "from typing import ClassVar, Literal",
        "",
        f"from node_dag.nodes.base import {BASES[req.node]}, Category",
        f"from node_dag.types import {', '.join(sorted(names))}",
        "",
        "",
        f"class {camel(req.name)}Config({BASES[req.node]}):",
        *docstring("    ", req.purpose, {"Scores": scores, "Args": fields}),
        "",
        f'    name: Literal["{req.name}"] = "{req.name}"',
        *(field_line(f) for f in req.config_fields),
        f"    categories = (Category.{CATEGORIES[req.node]},)",
        f'    inputs: ClassVar = {{"{req.port}": {kind}}}',
    ]
    if made:
        lines.append(f"    output: ClassVar = {made}")
    if scored:
        out = ", ".join(f'"{s}": Score' for s in scored)
        lines.append(f"    output: ClassVar = {{{out}}}")
    lines += wrap(BUMP_NOTE.format(name=req.name), "    ", "# ", "# ")
    lines.append("    version: ClassVar[int] = 1")
    return "\n".join(lines) + "\n"


def function_source(req: ToolRequest) -> str:
    """The ``function.py`` for ``req``, ``run`` typed and raising NotImplementedError."""
    cls, kind, (made, scored) = camel(req.name), TYPES[req.kind].__name__, outputs(req)
    names = {kind}
    if made:
        names.add(made)
        params, ret = f"{req.port}: list[{kind}]", f"list[{made}]"
        doc = f"Return the {req.output} made from each {req.kind}, or any number."
    elif scored:
        names.add("Score")
        params, ret = f"{req.port}: list[{kind}]", "list[dict[str, Score]]"
        doc = f"Return the scores {sorted(scored)} of each {req.kind}."
    else:
        params, ret = f"{req.port}: list[{kind}], values: list[float]", "list[bool]"
        doc = "Return True for each entity to keep on the yes branch."
    fields = ", ".join(f.name for f in req.config_fields) or "none"
    todo = (
        f"{req.name} is scaffolded but not written. It must: {req.purpose} "
        f"Config fields: {fields}. Worked example: {req.example}"
    )
    chunks = textwrap.wrap(todo, WIDTH - 14, drop_whitespace=False)
    return (
        "\n".join(
            [
                "from node_dag.nodes.base import BaseNode",
                f"from node_dag.nodes.{folder(req)}.{req.name}.config import {cls}Config",
                f"from node_dag.types import {', '.join(sorted(names))}",
                "",
                "",
                f"class {cls}(BaseNode[{cls}Config]):",
                *docstring("    ", req.purpose, {}),
                "",
                f"    def run(self, {params}) -> {ret}:",
                *docstring("        ", doc, {}),
                "        raise NotImplementedError(",
                *(f"            {json.dumps(c)}" for c in chunks),
                "        )",
            ]
        )
        + "\n"
    )


def factory_edits(req: ToolRequest) -> str:
    """The ``src/node_dag/factory.py`` edits, to make by hand."""
    cls, where = camel(req.name), f"node_dag.nodes.{folder(req)}.{req.name}"
    return f"""\
Make these edits to src/node_dag/factory.py, keeping each list in alphabetical order:
  from {where}.config import {cls}Config
  from {where}.function import {cls}
  NodeConfig:  | {cls}Config
  MAPPING:     {cls}Config: {cls},"""


def bump(path: Path) -> tuple[int, int]:
    """Raise the ``version`` in the config at ``path``, adding one below ``name`` if absent."""
    source = path.read_text()
    if m := VERSION.search(source):
        old = int(m["n"])
        line = f"{m['indent']}version: ClassVar[int] = {old + 1}"
        path.write_text(source[: m.start()] + line + source[m.end() :])
        return old, old + 1
    if not (m := NAME.search(source)):
        raise click.ClickException(
            f"{path} has no `name: Literal[...]` to put a version under."
        )
    path.write_text(
        source[: m.end()]
        + f"\n{m['indent']}version: ClassVar[int] = 2"
        + source[m.end() :]
    )
    return 1, 2


@click.command()
@click.argument("name")
@click.option("--force", is_flag=True, help="Overwrite the node if it already exists.")
@click.option(
    "--bump", "bump_", is_flag=True, help="Raise an existing node's version instead."
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where nodes live. Default: src/node_dag/nodes.",
)
def main(name: str, force: bool, bump_: bool, out_dir: Path | None) -> None:
    """Write the node that results/requests/NAME.json asks for, all but the body of run."""
    root = out_dir or Path(node_dag.nodes.__file__).parent
    if bump_:
        found = [
            p
            for d in ("tools", "filters")
            if (p := root / d / name / "config.py").exists()
        ]
        if not found:
            raise click.ClickException(f"No node called {name!r} under {root}.")
        old, new = bump(found[0])
        click.echo(f"{found[0]}: version {old} -> {new}. Restart the worker to use it.")
        return
    path = results_subdir("requests") / f"{name}.json"
    if not path.exists():
        raise click.ClickException(f"No tool request at {path}")
    req = ToolRequest.model_validate_json(path.read_bytes())
    if req.name != name:
        raise click.ClickException(
            f"{path} is a request for {req.name!r}, not {name!r}"
        )
    directory = root / folder(req) / name
    if directory.exists() and not force:
        raise click.ClickException(f"{directory} exists. Pass --force to overwrite it.")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text("")
    (directory / "config.py").write_text(config_source(req))
    (directory / "function.py").write_text(function_source(req))
    click.echo(
        f"Wrote {directory}/: config.py, and function.py, whose run raises NotImplementedError.\n"
    )
    click.echo(factory_edits(req))
    click.echo(
        "\nThen write run(), restart the worker, and click Resume on the nodes page."
        " Bump the version (--bump) after any later change to run()."
    )


if __name__ == "__main__":
    main()
