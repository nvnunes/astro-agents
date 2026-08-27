"""Recorded-command parsing and local script dependency discovery."""

from __future__ import annotations

import ast
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .discovery import (
    PATH_SUFFIXES,
    TOKEN_RE,
    data_index_path,
    resolve_reference,
)
from .discovery import (
    data_index as _data_index,
)
from .discovery import (
    expand_local_tokens as _expand_local_tokens,
)
from .python import python_local_dependencies

SCRIPT_SUFFIXES = frozenset({".ipynb", ".jl", ".m", ".py", ".r", ".sh"})
IGNORED_SCRIPT_PARTS = frozenset(
    {"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
PATH_PRESERVING_CALLS = frozenset(
    {
        "absolute",
        "expanduser",
        "joinpath",
        "path",
        "resolve",
        "with_name",
        "with_suffix",
    }
)


@dataclass(frozen=True)
class _PathArgumentContext:
    interface: Optional[Dict[str, Any]]
    entry_path: Path
    project_root: Path
    data_index: Dict[str, Any]
    workspace_roots: frozenset[Path]


@dataclass(frozen=True)
class _CommandContext:
    entry_path: Path
    project_root: Path
    data_index: Dict[str, Any]
    data_rows: Mapping[str, Mapping[str, Any]]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _argparse_flags(path: Path) -> Dict[str, Any]:
    try:
        tree = ast.parse(_read_text(path), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return {
            "parse": "fail",
            "error": str(exc),
            "flags": [],
            "positionals": [],
            "argument_roles": {},
        }
    flags = set()
    positionals = []
    destinations = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        declared = []
        for argument in node.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                declared.append(argument.value)
                if argument.value.startswith("-"):
                    flags.add(argument.value)
                else:
                    positionals.append((node.lineno, argument.value))
                    break
        explicit_dest = next(
            (
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "dest"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ),
            None,
        )
        if explicit_dest:
            destinations.add(explicit_dest)
        elif declared:
            destinations.add(
                next(
                    (value for value in declared if not value.startswith("-")),
                    declared[-1].lstrip("-").replace("-", "_"),
                )
            )
    return {
        "parse": "ok",
        "error": None,
        "flags": sorted(flags),
        "positionals": [name for _, name in sorted(positionals)],
        "argument_roles": _argument_roles_from_ast(tree, destinations),
    }


def _call_leaf_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id.lower()
    if isinstance(function, ast.Attribute):
        return function.attr.lower()
    return ""


def _open_call_role(call: ast.Call) -> Optional[str]:
    """Return the path role implied by builtin or bound ``open`` calls."""

    mode = None
    mode_index = 0 if isinstance(call.func, ast.Attribute) else 1
    if len(call.args) > mode_index:
        candidate = call.args[mode_index]
        if isinstance(candidate, ast.Constant):
            mode = candidate.value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = keyword.value.value
    if not isinstance(mode, str):
        return "input"
    return "output" if any(flag in mode for flag in "wax+") else "input"


def _call_path_role(call: ast.Call) -> Optional[str]:
    leaf = _call_leaf_name(call)
    if leaf == "open":
        return _open_call_role(call)
    if leaf in {
        "mkdir",
        "touch",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
        "savefig",
        "savetxt",
        "savez",
        "savez_compressed",
        "to_csv",
        "to_hdf",
        "to_json",
        "to_parquet",
        "to_pickle",
    } or re.search(r"(?:^|_)(?:write|save|dump|export|emit)(?:_|$)", leaf):
        return "output"
    if leaf in {
        "exists",
        "is_dir",
        "is_file",
        "loadmat",
        "read_bytes",
        "read_text",
    } or re.search(r"(?:^|_)(?:read|load|parse|inspect|scan)(?:_|$)", leaf):
        return "input"
    return None


def _represented_argument_destinations(
    value: ast.AST,
    destinations: set[str],
    known: Mapping[str, set[str]],
    local_preservers: Mapping[str, tuple[int, str]] | None = None,
) -> set[str]:
    """Return parsed arguments preserved by one bounded path expression."""

    if (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id in {"args", "parsed"}
        and value.attr in destinations
    ):
        return {value.attr}
    if isinstance(value, ast.Name):
        return set(known.get(value.id, set()))
    if isinstance(value, ast.Attribute):
        return _represented_argument_destinations(
            value.value, destinations, known, local_preservers
        )
    if isinstance(value, (ast.BoolOp, ast.BinOp, ast.IfExp)):
        return set().union(
            *(
                _represented_argument_destinations(
                    child, destinations, known, local_preservers
                )
                for child in ast.iter_child_nodes(value)
            )
        )
    values = _path_preserving_call_values(value, local_preservers or {})
    if not values:
        return set()
    return set().union(
        *(
            _represented_argument_destinations(
                child, destinations, known, local_preservers
            )
            for child in values
        )
    )


def _path_preserving_call_values(
    value: ast.AST, local_preservers: Mapping[str, tuple[int, str]]
) -> list[ast.AST]:
    """Return call values that preserve the represented path identity."""

    if not isinstance(value, ast.Call):
        return []
    if isinstance(value.func, ast.Name) and value.func.id in local_preservers:
        index, parameter = local_preservers[value.func.id]
        if index < len(value.args):
            return [value.args[index]]
        return [
            keyword.value
            for keyword in value.keywords
            if keyword.arg == parameter
        ]
    if _call_leaf_name(value) not in PATH_PRESERVING_CALLS:
        return []
    result: list[ast.AST] = list(value.args)
    if isinstance(value.func, ast.Attribute):
        result.append(value.func.value)
    return result


def _parsed_argument_aliases(
    tree: ast.AST,
    destinations: set[str],
    initial: Optional[Mapping[str, set[str]]] = None,
    local_preservers: Mapping[str, tuple[int, str]] | None = None,
) -> Dict[str, set[str]]:
    """Return path-preserving local aliases of parsed argument values."""

    aliases: Dict[str, set[str]] = {
        name: set(values) for name, values in (initial or {}).items()
    }
    assignments = [
        node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None:
                continue
            referenced = _represented_argument_destinations(
                value, destinations, aliases, local_preservers
            )
            for target in targets:
                if not isinstance(target, ast.Name) or not referenced:
                    continue
                current = aliases.setdefault(target.id, set())
                before = len(current)
                current.update(referenced)
                changed |= len(current) != before
        if not changed:
            break
    return aliases


def _local_path_preservers(tree: ast.AST) -> Dict[str, tuple[int, str]]:
    """Return local helpers whose every value return preserves one path."""

    result: Dict[str, tuple[int, str]] = {}
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        parameters = [
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        ]
        if not parameters:
            continue
        aliases = _parsed_argument_aliases(
            function,
            set(parameters),
            {parameter: {parameter} for parameter in parameters},
        )
        returns = [
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        represented = [
            _represented_argument_destinations(value, set(parameters), aliases)
            for value in returns
        ]
        if not represented or any(len(item) != 1 for item in represented):
            continue
        preserved = set.intersection(*represented)
        if len(preserved) != 1:
            continue
        parameter = next(iter(preserved))
        result[function.name] = (parameters.index(parameter), parameter)
    return result


def _argument_destinations(
    node: ast.AST, destinations: set[str], aliases: Mapping[str, set[str]]
) -> set[str]:
    """Return parsed arguments represented by one AST value use."""

    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"args", "parsed"}
        and node.attr in destinations
    ):
        return {node.attr}
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return set(aliases.get(node.id, set()))
    return set()


def _local_call_role(
    call: ast.Call,
    argument: ast.AST,
    roles: Mapping[str, Mapping[str, str]],
) -> tuple[bool, Optional[str]]:
    """Return a local helper parameter's inferred role for one call argument."""

    if not isinstance(call.func, ast.Name) or call.func.id not in roles:
        return False, None
    function_roles = roles[call.func.id]
    parameters = list(function_roles)
    index = next(
        (index for index, item in enumerate(call.args) if item is argument), None
    )
    if index is not None:
        role = (
            function_roles.get(parameters[index])
            if index < len(parameters)
            else None
        )
        return True, role
    keyword = next(
        (
            item
            for item in call.keywords
            if item.value is argument and isinstance(item.arg, str)
        ),
        None,
    )
    return (
        True,
        function_roles.get(keyword.arg)
        if keyword is not None and keyword.arg is not None
        else None,
    )


def _path_roles_for_use(
    node: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
    local_roles: Mapping[str, Mapping[str, str]],
    local_preservers: Mapping[str, tuple[int, str]],
) -> set[str]:
    """Return path roles implied by the bounded ancestors of one value use."""

    found: set[str] = set()
    current: Optional[ast.AST] = node
    for _ in range(8):
        if current is None:
            break
        argument = current
        current = parents.get(current)
        if current is None:
            break
        if isinstance(current, ast.keyword) and current.arg in {
            "cwd",
            "working_dir",
            "working_directory",
        }:
            found.add("workspace")
        if isinstance(
            current,
            (ast.JoinedStr, ast.ListComp, ast.SetComp, ast.GeneratorExp),
        ) and any(
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and "addpath(" in value.value.lower()
            for value in ast.walk(current)
        ):
            found.add("dependency-container")
        if isinstance(current, ast.Call):
            local, role = _local_call_role(current, argument, local_roles)
            if (
                local
                and isinstance(current.func, ast.Name)
                and current.func.id in local_preservers
            ):
                role = None
            if not local:
                role = _call_path_role(current)
            if role:
                found.add(role)
    return found


def _role_from_uses(found: set[str]) -> str:
    if len(found) == 1:
        return next(iter(found))
    if found == {"workspace", "input"}:
        return "workspace"
    if found == {"dependency-container", "input"}:
        return "dependency-container"
    return "unknown"


def _local_function_roles(
    tree: ast.AST,
    parents: Mapping[ast.AST, ast.AST],
    local_preservers: Mapping[str, tuple[int, str]],
) -> Dict[str, Dict[str, str]]:
    """Infer local helper parameter roles from their function bodies."""

    result: Dict[str, Dict[str, str]] = {}
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        parameters = [
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        ]
        uses: Dict[str, set[str]] = {parameter: set() for parameter in parameters}
        aliases: Dict[str, set[str]] = {
            parameter: {parameter} for parameter in parameters
        }
        aliases = _parsed_argument_aliases(
            function, set(parameters), aliases, local_preservers
        )
        for node in ast.walk(function):
            for parameter in _argument_destinations(node, set(parameters), aliases):
                uses[parameter].update(
                    _path_roles_for_use(node, parents, {}, local_preservers)
                )
        result[function.name] = {
            parameter: _role_from_uses(uses[parameter]) for parameter in parameters
        }
    return result


def _argument_roles_from_ast(
    tree: ast.AST, destinations: Iterable[str]
) -> Dict[str, str]:
    """Infer path roles from actual parsed-argument use in the entrypoint."""

    destination_set = set(destinations)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    local_preservers = _local_path_preservers(tree)
    aliases = _parsed_argument_aliases(
        tree, destination_set, local_preservers=local_preservers
    )
    local_roles = _local_function_roles(tree, parents, local_preservers)

    roles: Dict[str, set[str]] = {destination: set() for destination in destination_set}
    for node in ast.walk(tree):
        referenced = _argument_destinations(node, destination_set, aliases)
        found = (
            _path_roles_for_use(
                node, parents, local_roles, local_preservers
            )
            if referenced
            else set()
        )
        for destination in referenced:
            roles[destination].update(found)
    return {
        destination: _role_from_uses(found)
        for destination, found in roles.items()
    }


def _command_lines(block: Dict[str, Any]) -> List[str]:
    if block["kind"] != "command":
        return []
    logical: List[str] = []
    buffer = ""
    for raw in block["text"].splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("$"):
            line = line[1:].lstrip()
        buffer += (" " if buffer else "") + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            logical.append(buffer)
            buffer = ""
    if buffer:
        logical.append(buffer)
    return logical


def _path_argument_context(
    interface: Optional[Dict[str, Any]],
    entry_path: Path,
    project_root: Path,
    data_index: Dict[str, Any],
) -> _PathArgumentContext:
    workspace_roots = {
        project_root.resolve(),
        entry_path.parents[2].resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    system_temp_root = Path("/tmp")
    if system_temp_root.exists():
        workspace_roots.add(system_temp_root.resolve())
    return _PathArgumentContext(
        interface,
        entry_path,
        project_root,
        data_index,
        frozenset(workspace_roots),
    )


def _argument_values(
    tokens: Sequence[str],
    script_token: Optional[str],
    positionals: Sequence[str] = (),
) -> Iterable[tuple[int, Optional[str], str]]:
    skip_next = False
    positional_index = 0
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if index == 0 or token == script_token:
            continue
        option = None
        value = token
        if token.startswith("--") and "=" in token:
            option, value = token.split("=", 1)
        elif token.startswith("--"):
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                continue
            option = token
            value = tokens[index + 1]
            skip_next = True
        else:
            if positional_index < len(positionals):
                option = positionals[positional_index]
            positional_index += 1
        yield index, option, value


def _path_argument_role(
    path: Path,
    previous: str,
    option: Optional[str],
    indexed_names: set[str],
    context: _PathArgumentContext,
) -> str:
    if path in context.workspace_roots:
        return "workspace"
    if Path(previous).name == "tee" or previous in {">", ">>"}:
        return "output"
    parameter = (option or "").lstrip("-").replace("-", "_")
    source_role = (
        context.interface.get("argument_roles", {}).get(parameter, "unknown")
        if context.interface
        else "unknown"
    )
    if source_role != "unknown":
        return str(source_role)
    return "input" if indexed_names else "unknown"


def _dependency_container_paths(
    path: Path, path_value: str, entry_path: Path
) -> List[str]:
    paths = [path.resolve()]
    raw_path = Path(path_value)
    if not raw_path.is_absolute():
        log_relative = (entry_path.parents[2] / raw_path).resolve()
        if log_relative not in paths:
            paths.append(log_relative)
    return [item.as_posix() for item in paths if item.is_dir()]


def _path_arguments(
    tokens: Sequence[str],
    script_token: Optional[str],
    context: _PathArgumentContext,
) -> List[Dict[str, Any]]:
    results = []
    positionals = (
        context.interface.get("positionals", []) if context.interface else []
    )
    for index, option, value in _argument_values(
        tokens, script_token, positionals
    ):
        path_value = value.split("=", 1)[1] if "=" in value else value
        suffix = Path(path_value.split("#", 1)[0]).suffix.lower()
        path = _expand_local_tokens(
            path_value, context.entry_path, context.project_root, context.data_index
        )
        if path is None:
            continue
        previous = tokens[index - 1] if index else ""
        indexed_names = {
            name
            for name in TOKEN_RE.findall(path_value)
            if name not in {"project", "log"}
        }
        path_like = (
            bool(TOKEN_RE.search(path_value))
            or suffix in PATH_SUFFIXES
            or "/" in path_value
            or path.exists()
            or Path(previous).name == "tee"
            or previous in {">", ">>"}
        )
        if not path_like or any(character.isspace() for character in path_value):
            continue
        role_hint = _path_argument_role(
            path, previous, option, indexed_names, context
        )
        result: Dict[str, Any] = {
            "option": option,
            "raw": value,
            "path": path.as_posix(),
            "exists": path.exists(),
            "role_hint": role_hint,
        }
        if role_hint == "dependency-container":
            result["dependency_paths"] = _dependency_container_paths(
                path, path_value, context.entry_path
            )
        results.append(result)
    return results


def _invocation_tokens(tokens: Sequence[str]) -> List[str]:
    """Remove leading shell environment assignments from one invocation."""

    index = 0
    while index < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]):
        index += 1
    return list(tokens[index:])


def _option_values(
    tokens: Sequence[str], script_token: Optional[str]
) -> List[Dict[str, Optional[str]]]:
    """Return explicit option and positional values from one invocation."""

    return [
        {"option": option, "value": value}
        for _index, option, value in _argument_values(tokens, script_token)
    ]


def _script_token(invocation: Sequence[str]) -> Optional[str]:
    executable = Path(invocation[0]).name
    if len(invocation) > 1 and (
        executable == "pyrun" or executable.startswith("python")
    ):
        return invocation[1]
    return None


def _script_interface(
    script_token: Optional[str],
    entry_path: Path,
    project_root: Path,
    data_index: Dict[str, Any],
) -> tuple[Optional[Path], Optional[Dict[str, Any]]]:
    if script_token is None:
        return None, None
    path = _expand_local_tokens(script_token, entry_path, project_root, data_index)
    interface = (
        _argparse_flags(path)
        if path and path.suffix == ".py" and path.is_file()
        else None
    )
    return path, interface


def _data_token_results(
    command: str,
    data_rows: Mapping[str, Mapping[str, Any]],
    data_index: Mapping[str, Any],
    entry_path: Path,
    project_root: Path,
) -> List[Dict[str, Any]]:
    results = []
    for name in TOKEN_RE.findall(command):
        if name in {"project", "log"}:
            path = project_root if name == "project" else entry_path.parents[2]
            results.append(
                {"name": name, "status": "resolved", "path": path.as_posix()}
            )
        elif name in data_rows and name not in data_index["duplicates"]:
            location = data_rows[name].get("location", "")
            resolved = resolve_reference(
                str(location), data_index_path(data_index, entry_path)
            )
            results.append({"name": name, "status": "resolved", **resolved})
        else:
            status = "ambiguous" if name in data_index["duplicates"] else "unresolved"
            results.append({"name": name, "status": status})
    return results


def _command_record(
    block: Mapping[str, Any],
    command: str,
    context: _CommandContext,
) -> Optional[Dict[str, Any]]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return {
            "line": block["line"],
            "section": block["section"],
            "command": command,
            "error": str(exc),
        }
    invocation = _invocation_tokens(tokens)
    if not invocation:
        return None
    script_token = _script_token(invocation)
    script_path, interface = _script_interface(
        script_token, context.entry_path, context.project_root, context.data_index
    )
    options = sorted(
        {token.split("=", 1)[0] for token in tokens if token.startswith("--")}
    )
    unknown = (
        sorted(set(options) - set(interface["flags"]))
        if interface and interface["parse"] == "ok"
        else []
    )
    path_context = _path_argument_context(
        interface, context.entry_path, context.project_root, context.data_index
    )
    return {
        "line": block["line"],
        "section": block["section"],
        "command": command,
        "script": script_path.as_posix() if script_path else script_token,
        "script_token": script_token,
        "script_interface": interface,
        "options": options,
        "option_values": _option_values(invocation, script_token),
        "unknown_options": unknown,
        "data_tokens": _data_token_results(
            command,
            context.data_rows,
            context.data_index,
            context.entry_path,
            context.project_root,
        ),
        "path_arguments": _path_arguments(invocation, script_token, path_context),
    }


def _commands(
    parsed: Dict[str, Any],
    entry_path: Path,
    project_root: Path,
    script_inventory: Optional[set[Path]] = None,
) -> List[Dict[str, Any]]:
    commands: List[Dict[str, Any]] = []
    data_index = _data_index(entry_path)
    data_rows = {row.get("name", ""): row for row in data_index["rows"]}
    context = _CommandContext(entry_path, project_root, data_index, data_rows)
    for block in parsed["fenced_blocks"]:
        if block["section_type"] != "experimental":
            continue
        for command in _command_lines(block):
            record = _command_record(
                block, command, context
            )
            if record is None:
                continue
            if script_inventory:
                _extend_matlab_command_dependencies(
                    record, entry_path, project_root, script_inventory
                )
            commands.append(record)
    return commands


def _script_inventory(root: Path) -> List[Path]:
    """Return research scripts below one designated script root."""

    if not root.is_dir():
        return []
    return sorted(
        path.resolve()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SCRIPT_SUFFIXES
        and not any(part in IGNORED_SCRIPT_PARTS for part in path.parts)
    )


def _log_owned_roots(log_root: Path) -> List[Path]:
    """Return the log tree and targets reached through log-owned symlinks."""

    roots = {log_root.resolve()}
    for current, directories, files in os.walk(log_root, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            candidate = current_path / name
            if not candidate.is_symlink():
                continue
            try:
                roots.add(candidate.resolve(strict=True))
            except OSError:
                continue
    return sorted(roots)


def _path_is_log_owned(path: Path, owned_roots: Sequence[Path]) -> bool:
    """Return whether a resolved path lies on the log's logical file surface."""

    resolved = path.resolve()
    for root in owned_roots:
        if resolved == root:
            return True
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _python_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve statically identifiable local dependencies for one Python script."""

    return python_local_dependencies(path, inventory)


def _local_script_path(raw: str, parent: Path, inventory: set[Path]) -> Optional[Path]:
    if any(char in raw for char in "$`*?[]{}"):
        return None
    candidate = Path(raw.strip("'\""))
    if not candidate.is_absolute():
        candidate = parent / candidate
    candidate = candidate.resolve()
    return candidate if candidate in inventory else None


def _shell_line_dependencies(
    line: str, parent: Path, inventory: set[Path]
) -> set[Path]:
    candidates = set()
    source = re.match(r"^\s*(?:source|\.)\s+([^\s;&|]+)", line)
    if source:
        candidate = _local_script_path(source.group(1), parent, inventory)
        if candidate is not None:
            candidates.add(candidate)
    try:
        tokens = _invocation_tokens(shlex.split(line))
    except ValueError:
        return candidates
    if not tokens:
        return candidates
    interpreters = {"bash", "dash", "julia", "python", "python3", "rscript", "sh"}
    script_index = 1 if Path(tokens[0]).name.lower() in interpreters else 0
    if script_index < len(tokens):
        candidate = _local_script_path(tokens[script_index], parent, inventory)
        if candidate is not None:
            candidates.add(candidate)
    return candidates


def _shell_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve literal source and interpreter dependencies for one shell script."""

    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return []
    candidates = set().union(
        *(
            _shell_line_dependencies(line, path.parent, inventory)
            for line in text.splitlines()
        )
    )
    return sorted(candidates)


def _literal_source_dependencies(
    path: Path, inventory: set[Path], patterns: Sequence[str]
) -> List[Path]:
    """Resolve quoted local source/include paths for a research script."""

    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return []
    candidates = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = Path(match.group(1))
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if candidate in inventory:
                candidates.add(candidate)
    return sorted(candidates)


def _matlab_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve explicit local MATLAB file and same-folder function calls."""

    dependencies = set(
        _literal_source_dependencies(
            path,
            inventory,
            (r"\brun\s*\(\s*['\"]([^'\"]+\.m)['\"]",),
        )
    )
    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return sorted(dependencies)
    local_functions = {
        candidate.stem: candidate
        for candidate in inventory
        if candidate.parent == path.parent and candidate.suffix.lower() == ".m"
    }
    code = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    for name, candidate in local_functions.items():
        if candidate != path and re.search(rf"\b{re.escape(name)}\s*\(", code):
            dependencies.add(candidate)
    return sorted(dependencies)


def _split_matlab_arguments(value: str) -> List[str]:
    """Split one static MATLAB call argument list without evaluating it."""

    arguments = []
    buffer = []
    quote: Optional[str] = None
    depth = 0
    index = 0
    while index < len(value):
        character = value[index]
        if quote:
            buffer.append(character)
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    buffer.append(value[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            buffer.append(character)
        elif character in "([{":
            depth += 1
            buffer.append(character)
        elif character in ")]}" and depth:
            depth -= 1
            buffer.append(character)
        elif character == "," and depth == 0:
            arguments.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(character)
        index += 1
    if buffer or value.strip():
        arguments.append("".join(buffer).strip())
    return arguments


def _matlab_function_argument_roles(
    path: Path,
) -> Tuple[Optional[str], List[Tuple[str, str]]]:
    """Return one MATLAB function name and statically evident path roles."""

    try:
        text = _read_text(path)
    except (OSError, UnicodeError):
        return None, []
    match = re.search(
        r"^\s*function\s+(?:\[[^\]]*\]\s*=\s*|\w+\s*=\s*)?"
        r"([A-Za-z]\w*)\s*\(([^)]*)\)",
        text,
        flags=re.MULTILINE,
    )
    if not match:
        return None, []
    code = "\n".join(line.split("%", 1)[0] for line in text.splitlines())
    parameters = [item.strip() for item in match.group(2).split(",") if item.strip()]
    roles = []
    for parameter in parameters:
        escaped = re.escape(parameter)
        output = bool(
            re.search(
                rf"\b(?:resolve_output_path|writetable|writematrix|writecell|"
                rf"save)\s*\([^;\n]*\b{escaped}\b",
                code,
                flags=re.IGNORECASE,
            )
        )
        input_ = bool(
            re.search(
                rf"\b(?:resolve_existing_path|readtable|readmatrix|readcell|"
                rf"load)\s*\(\s*\b{escaped}\b",
                code,
                flags=re.IGNORECASE,
            )
        )
        role = "output" if output else "input" if input_ else "unknown"
        roles.append((parameter, role))
    return match.group(1), roles


def _static_matlab_path_argument(
    value: str, entry_path: Path, project_root: Path
) -> Optional[Path]:
    """Resolve one quoted static MATLAB path argument from a recorded command."""

    value = value.strip()
    if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
        return None
    raw = value[1:-1].replace(value[0] * 2, value[0])
    raw = raw.replace("<project>", project_root.as_posix())
    raw = raw.replace("<log>", entry_path.parents[2].as_posix())
    if TOKEN_RE.search(raw):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = entry_path.parent / path
    return path.resolve()


def _matlab_container_roots(command: Mapping[str, Any]) -> List[Path]:
    roots = []
    for argument in command.get("path_arguments", []):
        if argument.get("role_hint") != "dependency-container":
            continue
        for raw in argument.get("dependency_paths", [argument["path"]]):
            root = Path(raw).resolve()
            if root not in roots:
                roots.append(root)
    return roots


def _matlab_call(
    value: Any, roots: Sequence[Path], inventory: set[Path]
) -> Optional[tuple[Path, Sequence[Tuple[str, str]], str]]:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*([A-Za-z]\w*)\s*\((.*)\)\s*;?\s*", value, re.DOTALL)
    if not match:
        return None
    name = match.group(1)
    script = next(
        (
            candidate.resolve()
            for root in roots
            for candidate in [root / f"{name}.m"]
            if candidate.resolve() in inventory
        ),
        None,
    )
    if script is None:
        return None
    function_name, parameters = _matlab_function_argument_roles(script)
    return (script, parameters, match.group(2)) if function_name == name else None


def _matlab_path_arguments(
    option: Optional[str],
    parameters: Sequence[Tuple[str, str]],
    arguments: str,
    entry_path: Path,
    project_root: Path,
) -> List[Dict[str, Any]]:
    results = []
    for (_, role), value in zip(parameters, _split_matlab_arguments(arguments)):
        if role not in {"input", "output"}:
            continue
        path = _static_matlab_path_argument(value, entry_path, project_root)
        if path is not None:
            results.append(
                {
                    "option": option,
                    "raw": value,
                    "path": path.as_posix(),
                    "exists": path.exists(),
                    "role_hint": role,
                    "source": "matlab-command",
                }
            )
    return results


def _extend_matlab_command_dependencies(
    command: Dict[str, Any],
    entry_path: Path,
    project_root: Path,
    script_inventory: set[Path],
) -> None:
    """Add static MATLAB producer and path dependencies from a wrapper command."""

    container_roots = _matlab_container_roots(command)
    if not container_roots:
        return

    matlab_scripts = []
    added_arguments = []
    for option_value in command.get("option_values", []):
        call = _matlab_call(
            option_value.get("value"), container_roots, script_inventory
        )
        if call is None:
            continue
        script, parameters, arguments = call
        if script not in matlab_scripts:
            matlab_scripts.append(script)
        added_arguments.extend(
            _matlab_path_arguments(
                option_value.get("option"),
                parameters,
                arguments,
                entry_path,
                project_root,
            )
        )

    command["matlab_scripts"] = [path.as_posix() for path in matlab_scripts]
    existing = {
        (argument.get("path"), argument.get("role_hint"))
        for argument in command.get("path_arguments", [])
    }
    command["path_arguments"].extend(
        argument
        for argument in added_arguments
        if (argument["path"], argument["role_hint"]) not in existing
    )


def _script_local_dependencies(path: Path, inventory: set[Path]) -> List[Path]:
    """Resolve mechanically supported local dependency forms by script type."""

    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_local_dependencies(path, inventory)
    if suffix == ".sh":
        return _shell_local_dependencies(path, inventory)
    if suffix == ".m":
        return _matlab_local_dependencies(path, inventory)
    if suffix == ".r":
        return _literal_source_dependencies(
            path,
            inventory,
            (r"\b(?:source|sys\.source)\s*\(\s*['\"]([^'\"]+)['\"]",),
        )
    if suffix == ".jl":
        return _literal_source_dependencies(
            path, inventory, (r"\binclude\s*\(\s*['\"]([^'\"]+)['\"]",)
        )
    return []


def _complete_script_dependency_graph(
    inventory: set[Path],
) -> Dict[Path, List[Path]]:
    """Return all mechanically resolvable local code-dependency edges."""

    return {
        script: _script_local_dependencies(script, inventory)
        for script in sorted(inventory)
    }


argparse_flags = _argparse_flags
commands = _commands
script_inventory = _script_inventory
log_owned_roots = _log_owned_roots
path_is_log_owned = _path_is_log_owned
complete_script_dependency_graph = _complete_script_dependency_graph
