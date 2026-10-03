"""Turn a ToolRequest into the node package a human still has to fill in.

A ``ToolRequest`` carries the whole contract of a node that does not exist yet, and the
node layout is regular, so everything except the body of ``run`` can be generated. That
is what makes the blocked state cheap: the human step is writing the science, not the
boilerplate.

``--bump`` is the other half, and it matters more than it looks. ``RunNodeInput``
caches a node's result on its config, its inputs and ``config.version``, so fixing a bug
in ``run`` without raising ``version`` serves the stale, buggy result forever. Adding a
node while a run is blocked on it is exactly when that happens, which is why every
generated config carries ``version: ClassVar[int] = 1`` and a comment saying to bump it.
"""

import json
import re
import textwrap
from collections.abc import Sequence
from pathlib import Path

import click

import node_dag.nodes
from node_dag.plan import ConfigField, ToolRequest
from node_dag.types import TYPES
from temporal.store import requests_dir

WIDTH = 88

#: ``ConfigField.type`` to the annotation to write in the generated config.
ANNOTATIONS = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "list[str]": "tuple[str, ...]",  # BaseNodeConfig is frozen, so no mutable default.
}

_VERSION = re.compile(
    r"^(?P<indent>[ \t]*)version:\s*ClassVar\[int\]\s*=\s*(?P<n>\d+)[ \t]*$",
    re.MULTILINE,
)
_NAME = re.compile(r"^(?P<indent>[ \t]*)name:[ \t]*Literal\[.*$", re.MULTILINE)

BUMP_COMMENT = (
    "Part of the node result cache key, with the config and the inputs. Bump it after "
    "any change to run(), or the cached result of the old code is served forever."
)


def nodes_root() -> Path:
    """The ``src/node_dag/nodes`` directory a generated node package goes under."""
    return Path(node_dag.nodes.__file__).parent


def camel(name: str) -> str:
    """``recode_codons`` as ``RecodeCodons``."""
    return "".join(part.title() for part in name.split("_"))


def package(node: str) -> str:
    """``tools`` for a tool request, ``decisions`` for a decision one."""
    return "tools" if node == "tool" else "decisions"


def chunks(text: str, room: int) -> list[str]:
    """``text`` split on spaces into pieces of at most ``room`` characters."""
    out: list[str] = []
    rest = text
    while rest:
        if len(rest) <= room:
            out.append(rest)
            break
        cut = rest.rfind(" ", 0, room)
        cut = cut + 1 if cut > 0 else room
        out.append(rest[:cut])
        rest = rest[cut:]
    return out


def assign(indent: str, lhs: str, text: str) -> list[str]:
    """``lhs = "text"``, parenthesised across lines when it will not fit in one."""
    one = f"{indent}{lhs} = {json.dumps(text)}"
    if len(one) <= WIDTH:
        return [one]
    inner = indent + "    "
    lines = [f"{indent}{lhs} = ("]
    lines += [f"{inner}{json.dumps(c)}" for c in chunks(text, WIDTH - len(inner) - 2)]
    lines.append(f"{indent})")
    return lines


def wrap(text: str, indent: str, hanging: str | None = None) -> list[str]:
    """``text`` wrapped to ``WIDTH``, continuation lines at ``hanging``."""
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=hanging if hanging is not None else indent,
    ) or [indent.rstrip()]


def lower_first(text: str) -> str:
    """``text`` with its first letter lowered, so it reads inside a sentence."""
    stripped = text.strip()
    body = stripped[:1].lower() + stripped[1:]
    return body if body.endswith(".") else f"{body}."


def comment(text: str, indent: str) -> list[str]:
    """``text`` as a ``#`` comment, wrapped to ``WIDTH`` with every line prefixed."""
    return wrap(text, f"{indent}# ", f"{indent}# ")


def docstring(
    indent: str, summary: str, args: Sequence[tuple[str, str]] = ()
) -> list[str]:
    """A Google-style docstring: a wrapped summary, then an ``Args:`` block."""
    lines = textwrap.wrap(
        summary, width=WIDTH, initial_indent=f'{indent}"""', subsequent_indent=indent
    )
    if not args and len(lines) == 1 and len(lines[0]) + 3 <= WIDTH:
        return [f'{lines[0]}"""']
    if args:
        lines.append("")
        lines.append(f"{indent}Args:")
        for name, description in args:
            lines += wrap(
                f"{name}: {description}", f"{indent}    ", f"{indent}        "
            )
    lines.append(f'{indent}"""')
    return lines


def field_line(field: ConfigField) -> str:
    """One config field as a typed pydantic field, with its default when it has one."""
    line = f"    {field.name}: {ANNOTATIONS[field.type]}"
    if field.required and field.default is None:
        return line
    default = field.default
    if field.type == "list[str]" and isinstance(default, list):
        inner = ", ".join(json.dumps(v) for v in default)
        return f"{line} = ({inner},)" if len(default) == 1 else f"{line} = ({inner})"
    return f"{line} = {default!r}"


def kind_names(kinds: Sequence[str]) -> list[str]:
    """The ``node_dag.types`` class names for ``kinds``, sorted and deduplicated."""
    return sorted({TYPES[k].__name__ for k in kinds})


def config_source(req: ToolRequest) -> str:
    """The ``config.py`` for ``req``: its fields, its ports, and its cache version."""
    cls = f"{camel(req.name)}Config"
    base = "BaseToolConfig" if req.node == "tool" else "BaseDecisionConfig"
    used = [*req.inputs.values(), *([req.output] if req.output else [])]
    lines = [
        "from typing import ClassVar, Literal",
        "",
        f"from node_dag.nodes.base import {base}, Category",
        f"from node_dag.types import {', '.join(kind_names(used))}",
        "",
        "",
        f"class {cls}({base}):",
    ]
    args = [(f.name, f.description) for f in req.config_fields]
    lines += docstring("    ", req.purpose, args)
    lines.append("")
    lines.append(f'    name: Literal["{req.name}"] = "{req.name}"')
    lines += [field_line(f) for f in req.config_fields]
    lines.append(f"    categories = (Category.{req.category.name},)")
    ports = ", ".join(
        f'"{port}": {TYPES[kind].__name__}' for port, kind in req.inputs.items()
    )
    lines.append(f"    inputs: ClassVar = {{{ports}}}")
    if req.node == "tool":
        assert req.output is not None
        lines.append(f"    output: ClassVar = {TYPES[req.output].__name__}")
    else:
        lines.append(f'    forwards = "{req.forwards}"')
    lines += assign("    ", "example: ClassVar", req.example)
    lines += comment(BUMP_COMMENT, "    ")
    lines.append("    version: ClassVar[int] = 1")
    return "\n".join(lines) + "\n"


def signature(
    indent: str, ports: Sequence[str], types: Sequence[str], ret: str
) -> list[str]:
    """``def run(...) -> ret:``, broken across lines when it will not fit in one."""
    params = [f"{port}: {kind}" for port, kind in zip(ports, types, strict=True)]
    one = f"{indent}def run(self, {', '.join(params)}) -> {ret}:"
    if len(one) <= WIDTH:
        return [one]
    lines = [f"{indent}def run("]
    lines += [f"{indent}    self,"]
    lines += [f"{indent}    {p}," for p in params]
    lines.append(f"{indent}) -> {ret}:")
    return lines


def function_source(req: ToolRequest) -> str:
    """The ``function.py`` for ``req``, with ``run`` typed and left unimplemented."""
    cls, config_cls = camel(req.name), f"{camel(req.name)}Config"
    used = [*req.inputs.values(), *([req.output] if req.output else [])]
    ret = TYPES[req.output].__name__ if req.output else "bool"
    lines = [
        "from node_dag.nodes.base import BaseNode",
        f"from node_dag.nodes.{package(req.node)}.{req.name}.config import {config_cls}",
        f"from node_dag.types import {', '.join(kind_names(used))}",
        "",
        "",
        f"class {cls}(BaseNode[{config_cls}]):",
    ]
    lines += docstring("    ", req.purpose)
    lines.append("")
    ports = list(req.inputs)
    lines += signature(
        "    ", ports, [TYPES[k].__name__ for k in req.inputs.values()], ret
    )
    lines += docstring(
        "        ",
        "Return True for the yes branch."
        if req.node == "decision"
        else f"Return the {req.output} this node produces.",
    )
    takes = ", ".join(f"{port} ({kind})" for port, kind in req.inputs.items())
    must = (
        f"return True when the claim holds, forwarding {req.forwards!r} on <step>.yes"
        if req.node == "decision"
        else f"return a {req.output}"
    )
    fields = ", ".join(f.name for f in req.config_fields) or "none"
    body = (
        f"{req.name} is scaffolded but not written. It must {lower_first(req.purpose)}"
        f" It takes {takes} and must {must}. Config fields: {fields}. "
        f"Worked example: {req.example}"
    )
    lines.append("        raise NotImplementedError(")
    lines += [f"            {json.dumps(c)}" for c in chunks(body, WIDTH - 14)]
    lines.append("        )")
    return "\n".join(lines) + "\n"


def factory_edits(req: ToolRequest) -> str:
    """The two ``src/node_dag/factory.py`` edits, to apply by hand."""
    cls, config_cls = camel(req.name), f"{camel(req.name)}Config"
    where = f"node_dag.nodes.{package(req.node)}.{req.name}"
    return "\n".join(
        [
            "Two edits in src/node_dag/factory.py, which this does NOT apply:",
            "",
            "  1. the import lines, in alphabetical order with the others:",
            "",
            f"     from {where}.config import {config_cls}",
            f"     from {where}.function import {cls}",
            "",
            "  2a. the NodeConfig union member:",
            "",
            f"     | {config_cls}",
            "",
            "  2b. the MAPPING entry:",
            "",
            f"     {config_cls}: {cls},",
        ]
    )


def write_node(req: ToolRequest, root: Path, force: bool) -> Path:
    """Write ``__init__.py``, ``config.py`` and ``function.py`` for ``req``."""
    directory = root / package(req.node) / req.name
    if directory.exists() and not force:
        raise click.ClickException(
            f"{directory} already exists. Pass --force to overwrite it, or "
            f"--bump to raise the cache version of the node that is already there."
        )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "config.py").write_text(config_source(req), encoding="utf-8")
    (directory / "function.py").write_text(function_source(req), encoding="utf-8")
    return directory


def find_config(root: Path, name: str) -> Path:
    """The ``config.py`` of the node called ``name``, tool or decision."""
    for kind in ("tools", "decisions"):
        path = root / kind / name / "config.py"
        if path.exists():
            return path
    raise click.ClickException(
        f"No node called {name!r} under {root}. Scaffold it first, without --bump."
    )


def bump_version(path: Path) -> tuple[int, int]:
    """Raise the ``version`` ClassVar in ``path`` by one, and return old and new.

    A config that never declared one inherits ``BaseNodeConfig.version == 1``, so the
    next version is 2 and the line is inserted below ``name``.
    """
    source = path.read_text(encoding="utf-8")
    if match := _VERSION.search(source):
        old = int(match["n"])
        line = f"{match['indent']}version: ClassVar[int] = {old + 1}"
        path.write_text(source[: match.start()] + line + source[match.end() :], "utf-8")
        return old, old + 1
    name = _NAME.search(source)
    if name is None:
        raise click.ClickException(
            f"{path} declares neither 'version: ClassVar[int]' nor 'name: Literal[...]'"
            ", so there is nowhere to put the version. Add it by hand."
        )
    indent = name["indent"]
    added = "\n".join(
        [
            "",
            *comment(BUMP_COMMENT, indent),
            f"{indent}version: ClassVar[int] = 2",
        ]
    )
    path.write_text(source[: name.end()] + added + source[name.end() :], "utf-8")
    return 1, 2


@click.command()
@click.argument("name")
@click.option(
    "--force", is_flag=True, help="Overwrite the node directory if it already exists."
)
@click.option(
    "--bump",
    is_flag=True,
    help="Instead of scaffolding, raise the cache version of an existing node.",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Where the node package goes. Default: the installed src/node_dag/nodes.",
)
def main(name: str, force: bool, bump: bool, out_dir: Path | None) -> None:
    """Scaffold the node that results/requests/NAME.json asks for.

    Writes ``__init__.py``, ``config.py`` and a ``function.py`` whose ``run`` raises
    NotImplementedError, then prints the two factory.py edits to make by hand.
    """
    root = out_dir if out_dir is not None else nodes_root()
    if bump:
        path = find_config(root, name)
        old, new = bump_version(path)
        click.echo(f"{path}: version {old} -> {new}")
        click.echo(
            "The node result cache keys on config, inputs and version, so every "
            "cached result of the old run() is now ignored."
        )
        click.echo("Restart the worker for the new code to be picked up.")
        return

    path = requests_dir() / f"{name}.json"
    if not path.exists():
        raise click.ClickException(f"No tool request at {path}")
    req = ToolRequest.model_validate_json(path.read_text(encoding="utf-8"))
    if req.name != name:
        raise click.ClickException(
            f"{path} is a request for {req.name!r}, not {name!r}"
        )
    directory = write_node(req, root, force)
    click.echo(f"Wrote {directory / '__init__.py'}")
    click.echo(f"Wrote {directory / 'config.py'}")
    click.echo(f"Wrote {directory / 'function.py'}  (run raises NotImplementedError)")
    click.echo("")
    click.echo(factory_edits(req))
    click.echo("")
    click.echo("Then:")
    click.echo(
        "  - write the body of run(), and bump version in config.py after any later "
        "change to it: the node result cache keys on config + inputs + version, so a "
        "fix with no bump serves the old, buggy result forever."
    )
    click.echo("  - restart the worker, so factory.MAPPING picks the new node up.")
    click.echo(
        "  - then resume the hypothesis blocked on this node from the UI: "
        "'Tool added - resume' on its detail page, or at /requests to resume every "
        "run blocked on it at once."
    )


if __name__ == "__main__":
    main()
