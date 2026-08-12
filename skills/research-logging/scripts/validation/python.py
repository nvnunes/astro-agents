"""Static Python dependency discovery for research-log validation."""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path
from typing import Optional, Sequence

SCRIPT_SUFFIXES = {".ipynb", ".jl", ".m", ".py", ".r", ".sh"}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class PythonDependencyResolver:
    """Resolve statically identifiable local dependencies for one script."""

    def __init__(self, path: Path, inventory: set[Path], tree: ast.Module) -> None:
        self.path = path.resolve()
        self.inventory = inventory
        self.tree = tree
        self.candidates: set[Path] = set()
        self.import_roots = [path.parent]
        self.bindings: dict[str, tuple[Path, bool]] = {"__file__": (path, True)}
        self.path_calls: list[tuple[str, Path]] = []
        self.aliases: dict[str, str] = {}

    def static_path(self, node: ast.AST) -> Optional[tuple[Path, bool]]:
        """Evaluate the restricted path-expression subset used by wrappers."""

        direct = self._direct_static_path(node)
        if direct is not None:
            return direct
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            base = self.static_path(node.value)
            return (base[0].parent, base[1]) if base is not None else None
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            return self._static_parent_subscript(node)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self.static_path(node.left)
            right = self.static_path(node.right)
            if left is not None and right is not None:
                return left[0] / right[0], left[1] or right[1]
        return None

    def _direct_static_path(self, node: ast.AST) -> Optional[tuple[Path, bool]]:
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return Path(node.value), False
        if isinstance(node, ast.Call):
            return self._static_call_path(node)
        return None

    def _static_call_path(self, node: ast.Call) -> Optional[tuple[Path, bool]]:
        name = _call_name(node.func)
        if name in {"Path", "pathlib.Path", "str", "os.fspath"} and node.args:
            return self.static_path(node.args[0])
        if not isinstance(node.func, ast.Attribute):
            return None
        base = self.static_path(node.func.value)
        if base is None:
            return None
        value, anchored = base
        return self._static_path_method(node, value, anchored)

    def _static_path_method(
        self, node: ast.Call, value: Path, anchored: bool
    ) -> Optional[tuple[Path, bool]]:
        assert isinstance(node.func, ast.Attribute)
        if node.func.attr in {"resolve", "absolute", "expanduser"}:
            return value.resolve(), anchored
        if node.func.attr == "joinpath":
            return self._static_joinpath(value, anchored, node.args)
        if node.func.attr != "with_name" or len(node.args) != 1:
            return None
        name_value = self.static_path(node.args[0])
        return (
            (value.with_name(str(name_value[0])), anchored)
            if name_value is not None
            else None
        )

    def _static_joinpath(
        self, value: Path, anchored: bool, arguments: Sequence[ast.expr]
    ) -> Optional[tuple[Path, bool]]:
        result = value
        for argument in arguments:
            part = self.static_path(argument)
            if part is None:
                return None
            result /= part[0]
            anchored = anchored or part[1]
        return result, anchored

    def _static_parent_subscript(
        self, node: ast.Subscript
    ) -> Optional[tuple[Path, bool]]:
        if not isinstance(node.value, ast.Attribute) or node.value.attr != "parents":
            return None
        base = self.static_path(node.value.value)
        index = node.slice
        if (
            base is None
            or not isinstance(index, ast.Constant)
            or not isinstance(index.value, int)
        ):
            return None
        try:
            return base[0].parents[index.value], base[1]
        except IndexError:
            return None

    def bind_assignments(self) -> None:
        assignments = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ]
        for _ in range(4):
            changed = False
            for assignment in assignments:
                if assignment.value is None:
                    continue
                value = self.static_path(assignment.value)
                if value is None:
                    continue
                targets = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                for target in targets:
                    if (
                        isinstance(target, ast.Name)
                        and self.bindings.get(target.id) != value
                    ):
                        self.bindings[target.id] = value
                        changed = True
            if not changed:
                return

    def collect_path_calls(self, statements: Sequence[ast.stmt]) -> None:
        """Collect top-level sys.path mutations in runtime execution order."""

        for statement in statements:
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            if self._collect_static_loop(statement):
                continue
            nested = self._nested_statement_lists(statement)
            if nested:
                for statements_in_branch in nested:
                    self.collect_path_calls(statements_in_branch)
                continue
            for call in (
                node for node in ast.walk(statement) if isinstance(node, ast.Call)
            ):
                self._add_path_call(call)

    def _collect_static_loop(self, statement: ast.stmt) -> bool:
        if not (
            isinstance(statement, (ast.For, ast.AsyncFor))
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.iter, (ast.Tuple, ast.List))
        ):
            return False
        possible_values = [self.static_path(item) for item in statement.iter.elts]
        values = [value for value in possible_values if value is not None]
        if len(values) != len(possible_values):
            return False
        name = statement.target.id
        prior = self.bindings.get(name)
        had_prior = name in self.bindings
        for value in values:
            self.bindings[name] = value
            self.collect_path_calls(statement.body)
        if had_prior:
            assert prior is not None
            self.bindings[name] = prior
        else:
            self.bindings.pop(name, None)
        self.collect_path_calls(statement.orelse)
        return True

    @staticmethod
    def _nested_statement_lists(statement: ast.stmt) -> list[Sequence[ast.stmt]]:
        if isinstance(statement, ast.If):
            return [statement.body, statement.orelse]
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            return [statement.body]
        if isinstance(statement, ast.Try):
            return [
                statement.body,
                *(handler.body for handler in statement.handlers),
                statement.orelse,
                statement.finalbody,
            ]
        return []

    def _add_path_call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        value_node: Optional[ast.AST] = None
        if name == "sys.path.insert" and len(node.args) >= 2:
            index = node.args[0]
            if not (
                isinstance(index, ast.Constant)
                and isinstance(index.value, int)
                and index.value == 0
            ):
                return
            value_node = node.args[1]
        elif name == "sys.path.append" and node.args:
            value_node = node.args[0]
        if value_node is None:
            return
        resolved = self.static_path(value_node)
        if resolved is not None and resolved[1]:
            self.path_calls.append((name, resolved[0].resolve()))

    def apply_path_calls(self) -> None:
        for name, root in self.path_calls:
            if name == "sys.path.insert":
                self.import_roots.insert(0, root)
            else:
                self.import_roots.append(root)

    def collect_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._add_module(alias.name, 0)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._add_module(node.module, node.level)
                for alias in node.names:
                    if alias.name != "*":
                        child = ".".join(
                            part for part in (node.module, alias.name) if part
                        )
                        self._add_module(child, node.level)

    def _add_module(self, module: str, level: int) -> None:
        base = self.path.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        parts = module.split(".")
        roots = [base] if level else self.import_roots
        direct_match = self._first_module_match(roots, parts)
        if direct_match is not None:
            self.candidates.add(direct_match)
        elif level == 0:
            self._add_unique_inventory_module(parts)

    def _first_module_match(
        self, roots: Sequence[Path], parts: Sequence[str]
    ) -> Optional[Path]:
        for root in roots:
            for candidate in (
                root.joinpath(*parts, "__init__.py").resolve(),
                root.joinpath(*parts).with_suffix(".py").resolve(),
            ):
                if candidate in self.inventory:
                    return candidate
        return None

    def _add_unique_inventory_module(self, parts: Sequence[str]) -> None:
        direct_suffix = Path(*parts).with_suffix(".py").parts
        package_suffix = (*parts, "__init__.py")
        matches = [
            candidate
            for candidate in self.inventory
            if candidate.suffix.lower() == ".py"
            and (
                candidate.parts[-len(direct_suffix) :] == direct_suffix
                or candidate.parts[-len(package_suffix) :] == package_suffix
            )
        ]
        if len(matches) == 1:
            self.candidates.add(matches[0])

    def collect_anchored_paths(self) -> None:
        for node in ast.walk(self.tree):
            resolved = self.static_path(node)
            if resolved is None or not resolved[1]:
                continue
            candidate = resolved[0].resolve()
            if candidate in self.inventory and candidate != self.path:
                self.candidates.add(candidate)

    def collect_execution_dependencies(self) -> list[ast.Call]:
        calls = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call)]
        self._collect_aliases()
        execution_calls = {
            "os.execl",
            "os.execle",
            "os.execlp",
            "os.execlpe",
            "os.execv",
            "os.execve",
            "os.execvp",
            "os.execvpe",
            "os.system",
            "runpy.run_path",
            "subprocess.Popen",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.run",
        }
        for call in calls:
            if self.expanded_call_name(call.func) not in execution_calls:
                continue
            arguments = (*call.args, *(keyword.value for keyword in call.keywords))
            for argument in arguments:
                for node in ast.walk(argument):
                    self._add_execution_value(node)
        return calls

    def _collect_aliases(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.aliases[alias.asname or alias.name.split(".", 1)[0]] = (
                        alias.name
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self.aliases[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}"
                    )

    def expanded_call_name(self, node: ast.AST) -> str:
        name = _call_name(node)
        first, separator, rest = name.partition(".")
        replacement = self.aliases.get(first)
        if replacement is None:
            return name
        return replacement + (separator + rest if separator else "")

    def _add_execution_value(self, node: ast.AST) -> None:
        resolved = self.static_path(node)
        if resolved is not None:
            self._add_inventory_path(resolved[0])
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return
        try:
            tokens = shlex.split(node.value)
        except ValueError:
            tokens = [node.value]
        for token in tokens:
            if Path(token).suffix.lower() in SCRIPT_SUFFIXES:
                self._add_inventory_path(Path(token))

    def _add_inventory_path(self, candidate: Path) -> None:
        if not candidate.is_absolute():
            candidate = self.path.parent / candidate
        candidate = candidate.resolve()
        if candidate in self.inventory and candidate != self.path:
            self.candidates.add(candidate)

    def collect_matlab_dependencies(self, calls: Sequence[ast.Call]) -> None:
        if not any(
            "matlab" in self.expanded_call_name(call.func).lower() for call in calls
        ):
            return
        matlab_roots = {self.path.parent}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.JoinedStr):
                self._collect_matlab_roots(node, matlab_roots)
        local_matlab: dict[str, list[Path]] = {}
        for candidate in self.inventory:
            if candidate.parent in matlab_roots and candidate.suffix.lower() == ".m":
                local_matlab.setdefault(candidate.stem, []).append(candidate)
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for name in re.findall(r"(?<![A-Za-z0-9_])([A-Za-z]\w*)\s*\(", node.value):
                matches = local_matlab.get(name, [])
                if len(matches) == 1:
                    self.candidates.add(matches[0])

    def _collect_matlab_roots(
        self, node: ast.JoinedStr, matlab_roots: set[Path]
    ) -> None:
        literal = "".join(
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        )
        if "addpath(" not in literal:
            return
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            expression = value.value
            if isinstance(expression, ast.Call) and expression.args:
                expression = expression.args[0]
            resolved = self.static_path(expression)
            if resolved is not None and resolved[1]:
                matlab_roots.add(resolved[0].resolve())

    def resolve(self) -> list[Path]:
        self.bind_assignments()
        self.collect_path_calls(self.tree.body)
        self.apply_path_calls()
        self.collect_imports()
        self.collect_anchored_paths()
        calls = self.collect_execution_dependencies()
        self.collect_matlab_dependencies(calls)
        return sorted(self.candidates)


def python_local_dependencies(path: Path, inventory: set[Path]) -> list[Path]:
    """Resolve local Python, wrapper, and MATLAB dependencies without execution."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return []
    return PythonDependencyResolver(path, inventory, tree).resolve()
