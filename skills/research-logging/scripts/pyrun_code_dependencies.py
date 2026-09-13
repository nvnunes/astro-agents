"""Bounded static discovery of log-local Python dependencies."""

from __future__ import annotations

import ast
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from validation.filesystem import file_identity

MAX_CODE_PATHS = 256
MAX_SOURCE_BYTES = 1024 * 1024
MAX_ANALYSIS_WORK = 4_096
MAX_WARNINGS = 64

FileIdentity = tuple[int, int, int, int, int]


class CodeDiscoveryError(RuntimeError):
    """One unstable or unavailable source during static discovery."""


@dataclass(frozen=True)
class DiscoveredCodePath:
    """One stable logical helper path selected by static import analysis."""

    logical: Path
    resolved: Path
    identity: FileIdentity


@dataclass(frozen=True, order=True)
class CodeDiscoveryWarning:
    """One nonblocking reason that static dependency coverage is incomplete."""

    path: str
    line: int
    code: str
    detail: str


@dataclass(frozen=True)
class CodeDiscovery:
    """The bounded helper set and ordered incomplete-coverage warnings."""

    paths: tuple[DiscoveredCodePath, ...]
    warnings: tuple[CodeDiscoveryWarning, ...]


@dataclass(frozen=True)
class _ModuleContext:
    root: Path
    package: tuple[str, ...]


@dataclass(frozen=True)
class _ResolvedSource:
    path: Path
    context: _ModuleContext


def discover_code(
    script: Path, *, entry_root: Path, log_root: Path
) -> CodeDiscovery:
    """Discover ordinary static imports without importing or executing code."""

    return _Analyzer(script, entry_root=entry_root, log_root=log_root).discover()


class _Analyzer:
    def __init__(self, script: Path, *, entry_root: Path, log_root: Path) -> None:
        self._script = Path(os.path.abspath(script))
        self._entry_root = Path(os.path.abspath(entry_root))
        self._log_root = Path(os.path.abspath(log_root))
        self._roots = _unique_paths(
            (
                self._script.parent,
                self._entry_root / "scripts",
                self._log_root / "scripts",
            )
        )
        self._script_resolved = self._script.resolve(strict=True)
        self._visited: set[Path] = set()
        self._selected: dict[Path, DiscoveredCodePath] = {}
        self._warnings: list[CodeDiscoveryWarning] = []
        self._work = 0
        self._code_limit_reported = False
        self._work_limit_reported = False
        self._warning_limit_reported = False

    def discover(self) -> CodeDiscovery:
        self._visit(self._script, _ModuleContext(self._script.parent, ()), False)
        paths = tuple(
            sorted(
                self._selected.values(),
                key=lambda value: _path_bytes(value.logical),
            )
        )
        return CodeDiscovery(paths, tuple(sorted(self._warnings)))

    def _visit(
        self, logical: Path, context: _ModuleContext, include: bool
    ) -> None:
        observed = self._observe_source(logical)
        if observed.resolved in self._visited:
            if include:
                self._retain_alias(observed)
            return
        self._visited.add(observed.resolved)
        if include and observed.resolved != self._script_resolved:
            if len(self._selected) >= MAX_CODE_PATHS:
                if not self._code_limit_reported:
                    self._warn(
                        logical,
                        0,
                        "helper_limit",
                        f"static helper discovery exceeds {MAX_CODE_PATHS} paths",
                    )
                    self._code_limit_reported = True
                return
            self._retain_alias(observed)
        if self._work >= MAX_ANALYSIS_WORK:
            if not self._work_limit_reported:
                self._warn(
                    logical,
                    0,
                    "analysis_limit",
                    f"static import analysis exceeds {MAX_ANALYSIS_WORK} work items",
                )
                self._work_limit_reported = True
            return
        self._work += 1
        tree = self._parse(logical)
        if tree is None:
            return
        self._warn_for_unsupported_constructs(logical, tree)
        imports = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in imports:
            self._follow_import(logical, context, node)

    def _observe_source(self, logical: Path) -> DiscoveredCodePath:
        logical = Path(os.path.abspath(logical))
        try:
            before = logical.stat()
            resolved = logical.resolve(strict=True)
        except OSError as error:
            raise CodeDiscoveryError(
                f"static code source unavailable: {logical}: {error}"
            ) from error
        if not stat.S_ISREG(before.st_mode):
            raise CodeDiscoveryError(f"static code source is not a file: {logical}")
        return DiscoveredCodePath(logical, resolved, file_identity(before))

    def _parse(self, logical: Path) -> ast.AST | None:
        try:
            before = logical.stat()
            if before.st_size > MAX_SOURCE_BYTES:
                self._warn(
                    logical,
                    0,
                    "source_limit",
                    f"source exceeds {MAX_SOURCE_BYTES} bytes",
                )
                return None
            source = logical.read_bytes()
            after = logical.stat()
        except OSError as error:
            raise CodeDiscoveryError(
                f"static code source unavailable: {logical}: {error}"
            ) from error
        if file_identity(before) != file_identity(after):
            raise CodeDiscoveryError(
                f"static code source changed during discovery: {logical}"
            )
        try:
            return ast.parse(source, filename=str(logical))
        except (SyntaxError, ValueError) as error:
            self._warn(
                logical,
                getattr(error, "lineno", 0) or 0,
                "parse_unsupported",
                str(error),
            )
            return None

    def _follow_import(
        self,
        logical: Path,
        context: _ModuleContext,
        node: ast.Import | ast.ImportFrom,
    ) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._follow_module(logical, node.lineno, tuple(alias.name.split(".")))
            return
        module = tuple(node.module.split(".")) if node.module else ()
        if node.level:
            drop = node.level - 1
            if drop > len(context.package):
                self._warn(
                    logical,
                    node.lineno,
                    "relative_import_unresolved",
                    "relative import escapes package: "
                    f"{'.' * node.level}{node.module or ''}",
                )
                return
            base = context.package[: len(context.package) - drop]
            target = (*base, *module)
            resolved = self._resolve_module(target, (context.root,))
            if resolved is None:
                self._warn(
                    logical,
                    node.lineno,
                    "relative_import_unresolved",
                    "unresolved relative import: "
                    f"{'.' * node.level}{node.module or ''}",
                )
                return
            self._visit_resolved(resolved)
            self._follow_concrete_members(context.root, target, node)
            return
        if not module:
            return
        resolved = self._resolve_module(module, self._roots)
        if resolved is None:
            if self._local_head_exists(module[0]):
                self._warn(
                    logical,
                    node.lineno,
                    "local_import_unresolved",
                    f"unresolved log-local import: {'.'.join(module)}",
                )
            return
        self._visit_resolved(resolved)
        self._follow_concrete_members(resolved[-1].context.root, module, node)

    def _follow_module(
        self, logical: Path, line: int, module: tuple[str, ...]
    ) -> None:
        resolved = self._resolve_module(module, self._roots)
        if resolved is None:
            if module and self._local_head_exists(module[0]):
                self._warn(
                    logical,
                    line,
                    "local_import_unresolved",
                    f"unresolved log-local import: {'.'.join(module)}",
                )
            return
        self._visit_resolved(resolved)

    def _follow_concrete_members(
        self, root: Path, module: tuple[str, ...], node: ast.ImportFrom
    ) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            resolved = self._resolve_module((*module, alias.name), (root,))
            if resolved is not None:
                self._visit_resolved(resolved)

    def _resolve_module(
        self, module: tuple[str, ...], roots: Iterable[Path]
    ) -> tuple[_ResolvedSource, ...] | None:
        if not module or any(not part.isidentifier() for part in module):
            return None
        for root in roots:
            resolved = _module_sources(root, module, self._log_root)
            if resolved is not None:
                return resolved
        return None

    def _visit_resolved(self, sources: tuple[_ResolvedSource, ...]) -> None:
        for source in sources:
            self._visit(source.path, source.context, True)

    def _local_head_exists(self, name: str) -> bool:
        return any(
            (root / f"{name}.py").is_file()
            or (root / name / "__init__.py").is_file()
            for root in self._roots
        )

    def _retain_alias(self, observed: DiscoveredCodePath) -> None:
        previous = self._selected.get(observed.resolved)
        if previous is None or _path_bytes(observed.logical) < _path_bytes(
            previous.logical
        ):
            self._selected[observed.resolved] = observed

    def _warn_for_unsupported_constructs(self, logical: Path, tree: ast.AST) -> None:
        warnings: set[tuple[int, str, str]] = set()
        for node in ast.walk(tree):
            if _mutates_sys_path(node):
                warnings.add(
                    (
                        getattr(node, "lineno", 0),
                        "import_path_mutation",
                        "runtime import-path changes are not followed",
                    )
                )
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in {"__import__", "importlib.import_module"}:
                warnings.add(
                    (node.lineno, "dynamic_import", "dynamic import is not followed")
                )
            elif name in {"eval", "exec"}:
                warnings.add(
                    (node.lineno, "dynamic_code", f"{name} code is not analyzed")
                )
            elif name.startswith("sys.path."):
                warnings.add(
                    (
                        node.lineno,
                        "import_path_mutation",
                        "runtime import-path changes are not followed",
                    )
                )
            elif name.startswith("subprocess.") or name.startswith("multiprocessing."):
                warnings.add(
                    (
                        node.lineno,
                        "descendant_code",
                        "child-process Python entrypoints are not followed",
                    )
                )
        for line, code, detail in sorted(warnings):
            self._warn(logical, line, code, detail)

    def _warn(self, logical: Path, line: int, code: str, detail: str) -> None:
        warning = CodeDiscoveryWarning(
            _display_path(logical, self._log_root), line, code, detail
        )
        if len(self._warnings) < MAX_WARNINGS:
            self._warnings.append(warning)
            return
        if self._warning_limit_reported:
            return
        self._warnings[-1] = CodeDiscoveryWarning(
            "<log>",
            0,
            "warning_limit",
            f"static dependency warnings exceed {MAX_WARNINGS}",
        )
        self._warning_limit_reported = True


def _module_sources(
    root: Path, module: tuple[str, ...], log_root: Path
) -> tuple[_ResolvedSource, ...] | None:
    current = Path(os.path.abspath(root))
    sources: list[_ResolvedSource] = []
    package: list[str] = []
    for part in module[:-1]:
        current /= part
        initializer = current / "__init__.py"
        if not _eligible(initializer, log_root):
            return None
        package.append(part)
        sources.append(
            _ResolvedSource(initializer, _ModuleContext(root, tuple(package)))
        )
    leaf = module[-1]
    package_initializer = current / leaf / "__init__.py"
    module_file = current / f"{leaf}.py"
    if _eligible(package_initializer, log_root):
        package.append(leaf)
        sources.append(
            _ResolvedSource(
                package_initializer, _ModuleContext(root, tuple(package))
            )
        )
    elif _eligible(module_file, log_root):
        sources.append(
            _ResolvedSource(module_file, _ModuleContext(root, tuple(package)))
        )
    else:
        return None
    return tuple(sources)


def _eligible(path: Path, log_root: Path) -> bool:
    logical = Path(os.path.abspath(path))
    try:
        logical.relative_to(log_root)
        observation = logical.stat()
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(observation.st_mode) and logical.suffix == ".py"


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in paths:
        path = Path(os.path.abspath(path))
        if path not in result:
            result.append(path)
    return tuple(result)


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _mutates_sys_path(node: ast.AST) -> bool:
    targets: tuple[ast.expr, ...]
    if isinstance(node, ast.Assign):
        targets = tuple(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = (node.target,)
    elif isinstance(node, ast.Delete):
        targets = tuple(node.targets)
    else:
        return False
    return any(_call_name(target).startswith("sys.path") for target in targets)


def _display_path(path: Path, log_root: Path) -> str:
    logical = Path(os.path.abspath(path))
    try:
        return f"<log>/{logical.relative_to(log_root).as_posix()}"
    except ValueError:
        return logical.as_posix()


def _path_bytes(path: Path) -> bytes:
    return path.as_posix().encode("utf-8")
