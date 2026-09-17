"""Deterministic fingerprints of statically reachable project-local Python."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Union

from validation.filesystem import file_identity

FINGERPRINT_ALGORITHM = "python-effective-code-sha256-v1"
MAX_SOURCE_FILES = 256
MAX_SOURCE_BYTES = 1024 * 1024
MAX_UNSUPPORTED_LOCATIONS = 64

FileIdentity = tuple[int, int, int, int, int]


class EffectiveCodeError(RuntimeError):
    """An operational failure prevented trustworthy source analysis."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        path: str | None = None,
        line: int = 0,
    ) -> None:
        self.code = code
        self.detail = detail
        self.path = path
        self.line = line
        location = f" {path}" if path else ""
        if path and line:
            location += f":{line}"
        super().__init__(f"{code}:{location}: {detail}")


@dataclass(frozen=True)
class EffectiveCodeFingerprint:
    """One versioned semantic fingerprint."""

    algorithm: str
    digest: str


@dataclass(frozen=True, order=True)
class UnsupportedLocation:
    """One actionable reason an effective fingerprint is unavailable."""

    path: str
    line: int
    construct: str
    detail: str


@dataclass(frozen=True)
class EffectiveCodeAnalysis:
    """A complete fingerprint or a bounded unsupported result, never both."""

    fingerprint: EffectiveCodeFingerprint | None
    unsupported: tuple[UnsupportedLocation, ...]
    unsupported_truncated: bool = False

    def __post_init__(self) -> None:
        if (self.fingerprint is None) == (not self.unsupported):
            raise ValueError("effective-code analysis requires exactly one result kind")


@dataclass(frozen=True)
class _ModuleContext:
    root: Path
    package: tuple[str, ...]
    module: tuple[str, ...]


@dataclass(frozen=True)
class _ModuleTarget:
    path: Path
    context: _ModuleContext


@dataclass(frozen=True)
class _NamespaceTarget:
    context: _ModuleContext


@dataclass(frozen=True)
class _DefinitionTarget:
    path: Path
    qualname: tuple[str, ...]


@dataclass(frozen=True)
class _ClassInstance:
    definition: _DefinitionTarget


@dataclass(frozen=True)
class _LocalValue:
    path: Path
    name: str


@dataclass(frozen=True)
class _External:
    name: str


@dataclass(frozen=True)
class _Unknown:
    name: str = ""


_Binding = Union[
    _ModuleTarget,
    _NamespaceTarget,
    _DefinitionTarget,
    _ClassInstance,
    _LocalValue,
    _External,
    _Unknown,
]


@dataclass
class _Source:
    path: Path
    relative: str
    identity: FileIdentity
    tree: ast.Module
    context: _ModuleContext
    definitions: dict[
        tuple[str, ...], ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ] = field(default_factory=dict)
    module_names: dict[str, _Binding] = field(default_factory=dict)
    active: bool = False
    analyzed: bool = False


@dataclass
class _Scope:
    source: _Source
    analyzer: _Analyzer
    parent: _Scope | None = None
    owner_class: _DefinitionTarget | None = None
    names: dict[str, _Binding] = field(default_factory=dict)

    def resolve(self, name: str) -> _Binding | None:
        if name in self.names:
            return self.names[name]
        if self.parent is not None:
            return self.parent.resolve(name)
        return self.source.module_names.get(name)


def analyze_effective_code(
    script: Path,
    *,
    project_root: Path,
    import_roots: tuple[Path, ...] | None = None,
) -> EffectiveCodeAnalysis:
    """Analyze *script* without importing it or executing project code."""

    return _Analyzer(
        script=script,
        project_root=project_root,
        import_roots=import_roots,
    ).analyze()


class _Analyzer:
    def __init__(
        self,
        *,
        script: Path,
        project_root: Path,
        import_roots: tuple[Path, ...] | None,
    ) -> None:
        self._project_root = Path(os.path.abspath(project_root)).resolve(strict=True)
        self._script = Path(os.path.abspath(script))
        self._import_roots = self._validated_import_roots(import_roots)
        self._sources: dict[Path, _Source] = {}
        self._module_targets: dict[
            tuple[Path, tuple[str, ...]], _ModuleTarget | _NamespaceTarget
        ] = {}
        self._class_bases: dict[_DefinitionTarget, tuple[_Binding | None, ...]] = {}
        self._reached: set[_DefinitionTarget] = set()
        self._analyzing: set[_DefinitionTarget] = set()
        self._unsupported: set[UnsupportedLocation] = set()

    def analyze(self) -> EffectiveCodeAnalysis:
        script = self._load_source(
            self._script,
            _ModuleContext(self._script.parent, (), ()),
        )
        self._activate(script)
        unsupported = tuple(sorted(self._unsupported))
        truncated = len(unsupported) > MAX_UNSUPPORTED_LOCATIONS
        if unsupported:
            return EffectiveCodeAnalysis(
                None,
                unsupported[:MAX_UNSUPPORTED_LOCATIONS],
                unsupported_truncated=truncated,
            )
        payload = self._fingerprint_payload()
        digest = hashlib.sha256(payload).hexdigest()
        return EffectiveCodeAnalysis(
            EffectiveCodeFingerprint(FINGERPRINT_ALGORITHM, digest), ()
        )

    def _load_source(self, path: Path, context: _ModuleContext) -> _Source:
        logical = Path(os.path.abspath(path))
        display = self._display(logical)
        try:
            before = logical.stat()
            resolved = logical.resolve(strict=True)
        except OSError as error:
            raise EffectiveCodeError(
                "effective_code.source_unavailable",
                str(error),
                path=display,
            ) from error
        if not stat.S_ISREG(before.st_mode):
            raise EffectiveCodeError(
                "effective_code.source_invalid",
                "source is not a regular file",
                path=display,
            )
        if resolved.suffix != ".py" or not self._inside_project(resolved):
            raise EffectiveCodeError(
                "effective_code.source_outside_project",
                "Python source must resolve inside the project",
                path=display,
            )
        known = self._sources.get(resolved)
        if known is not None:
            self._remember_module(known, context)
            return known
        if len(self._sources) >= MAX_SOURCE_FILES:
            raise EffectiveCodeError(
                "effective_code.source_limit",
                f"analysis exceeds {MAX_SOURCE_FILES} project-local source files",
                path=display,
            )
        if before.st_size > MAX_SOURCE_BYTES:
            raise EffectiveCodeError(
                "effective_code.source_size_limit",
                f"source exceeds {MAX_SOURCE_BYTES} bytes",
                path=display,
            )
        try:
            content = logical.read_bytes()
            after = logical.stat()
        except OSError as error:
            raise EffectiveCodeError(
                "effective_code.source_unavailable",
                str(error),
                path=display,
            ) from error
        if file_identity(before) != file_identity(after):
            raise EffectiveCodeError(
                "effective_code.source_changed",
                "source changed during analysis",
                path=display,
            )
        try:
            tree = ast.parse(content, filename=str(logical))
        except (SyntaxError, ValueError) as error:
            raise EffectiveCodeError(
                "effective_code.syntax_invalid",
                str(error),
                path=display,
                line=getattr(error, "lineno", 0) or 0,
            ) from error
        source = _Source(
            resolved,
            resolved.relative_to(self._project_root).as_posix(),
            file_identity(after),
            tree,
            context,
        )
        self._sources[resolved] = source
        self._index_scope(source, tree.body, ())
        self._remember_module(source, context)
        return source

    def _remember_module(self, source: _Source, context: _ModuleContext) -> None:
        if context.module:
            self._module_targets[(context.root, context.module)] = _ModuleTarget(
                source.path, context
            )

    def _activate_target(self, target: _ModuleTarget | _NamespaceTarget) -> None:
        if isinstance(target, _ModuleTarget):
            self._activate(self._sources[target.path])

    def _index_scope(
        self,
        source: _Source,
        statements: Iterable[ast.stmt],
        prefix: tuple[str, ...],
    ) -> dict[str, _Binding]:
        names: dict[str, _Binding] = {}
        for statement in _lexical_statements(statements):
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                target = _DefinitionTarget(source.path, (*prefix, statement.name))
                source.definitions[target.qualname] = statement
                names[statement.name] = target
                if isinstance(statement, ast.ClassDef):
                    self._index_scope(source, statement.body, target.qualname)
                continue
            for name in _assigned_names(statement):
                names.setdefault(name, _LocalValue(source.path, name))
        if not prefix:
            source.module_names.update(names)
        return names

    def _activate(self, source: _Source) -> None:
        if source.analyzed or source.active:
            return
        source.active = True
        try:
            scope = _Scope(source, self, names=source.module_names)
            visitor = _Visitor(scope)
            for statement in source.tree.body:
                visitor.visit(statement)
            source.analyzed = True
        finally:
            source.active = False

    def resolve_import(
        self,
        source: _Source,
        node: ast.Import | ast.ImportFrom,
    ) -> tuple[tuple[str, _Binding], ...]:
        if isinstance(node, ast.Import):
            return self._resolve_plain_import(source, node)
        return self._resolve_from_import(source, node)

    def _resolve_plain_import(
        self, source: _Source, node: ast.Import
    ) -> tuple[tuple[str, _Binding], ...]:
        result: list[tuple[str, _Binding]] = []
        for alias in node.names:
            module = tuple(alias.name.split("."))
            targets = self._resolve_module(module, self._roots(source))
            if targets is None:
                result.append((alias.asname or module[0], _External(alias.name)))
                continue
            for target in targets:
                self._activate_target(target)
            bound = targets[-1] if alias.asname else targets[0]
            result.append((alias.asname or module[0], bound))
        return tuple(result)

    def _resolve_from_import(
        self, source: _Source, node: ast.ImportFrom
    ) -> tuple[tuple[str, _Binding], ...]:
        if any(alias.name == "*" for alias in node.names):
            self.unsupported(
                source,
                node,
                "wildcard_import",
                "wildcard imports cannot be resolved deterministically",
            )
            return ()
        target_module = self._relative_module(source, node)
        if target_module is None:
            module_name = node.module or ""
            return tuple(
                (
                    alias.asname or alias.name,
                    _External(f"{module_name}.{alias.name}".strip(".")),
                )
                for alias in node.names
            )
        module_target = target_module[-1]
        for target in target_module:
            self._activate_target(target)
        result = []
        for alias in node.names:
            binding = self._module_member(module_target, alias.name)
            if binding is None:
                child = self._resolve_module(
                    (*module_target.context.module, alias.name),
                    (module_target.context.root,),
                )
                if child is not None:
                    for target in child:
                        self._activate_target(target)
                    binding = child[-1]
            if binding is None:
                self.unsupported(
                    source,
                    node,
                    "local_attribute_unresolved",
                    "project-local import has no statically visible member "
                    f"{alias.name!r}",
                )
                binding = _Unknown(alias.name)
            result.append((alias.asname or alias.name, binding))
        return tuple(result)

    def _relative_module(
        self, source: _Source, node: ast.ImportFrom
    ) -> tuple[_ModuleTarget | _NamespaceTarget, ...] | None:
        module = tuple(node.module.split(".")) if node.module else ()
        if not node.level:
            if not module:
                return None
            resolved = self._resolve_module(module, self._roots(source))
            if resolved is not None:
                return resolved
            return None
        drop = node.level - 1
        package = source.context.package
        if drop > len(package):
            self.unsupported(
                source,
                node,
                "relative_import_unresolved",
                "relative import escapes its package",
            )
            return None
        target = (*package[: len(package) - drop], *module)
        resolved = self._resolve_module(target, (source.context.root,))
        if resolved is None:
            self.unsupported(
                source,
                node,
                "relative_import_unresolved",
                "project-local relative import "
                f"{'.' * node.level}{node.module or ''} is unresolved",
            )
        return resolved

    def _roots(self, source: _Source) -> tuple[Path, ...]:
        del source
        return self._import_roots

    def _validated_import_roots(
        self, import_roots: tuple[Path, ...] | None
    ) -> tuple[Path, ...]:
        if import_roots is None:
            return _unique_paths((self._script.parent, self._project_root))
        roots = import_roots
        result = _unique_paths(Path(os.path.abspath(path)) for path in roots)
        for root in result:
            if not self._inside_project(root.resolve(strict=False)):
                raise EffectiveCodeError(
                    "effective_code.import_root_outside_project",
                    "Python import roots must resolve inside the project",
                    path=root.as_posix(),
                )
        return result

    def _resolve_module(
        self, module: tuple[str, ...], roots: Iterable[Path]
    ) -> tuple[_ModuleTarget | _NamespaceTarget, ...] | None:
        if not module or any(not part.isidentifier() for part in module):
            return None
        for root in roots:
            targets = self._module_sources(Path(os.path.abspath(root)), module)
            if targets is not None:
                return targets
        return None

    def _module_sources(
        self, root: Path, module: tuple[str, ...]
    ) -> tuple[_ModuleTarget | _NamespaceTarget, ...] | None:
        current = root
        result: list[_ModuleTarget | _NamespaceTarget] = []
        package: list[str] = []
        for part in module[:-1]:
            current /= part
            initializer = current / "__init__.py"
            package.append(part)
            context = _ModuleContext(root, tuple(package), tuple(package))
            if self._eligible(initializer):
                source = self._load_source(initializer, context)
                target: _ModuleTarget | _NamespaceTarget = _ModuleTarget(
                    source.path, context
                )
            elif self._eligible_namespace(current):
                target = _NamespaceTarget(context)
                self._module_targets[(root, context.module)] = target
            else:
                return None
            result.append(target)
        leaf = module[-1]
        initializer = current / leaf / "__init__.py"
        module_file = current / f"{leaf}.py"
        if self._eligible(initializer):
            package.append(leaf)
            context = _ModuleContext(root, tuple(package), tuple(module))
            source = self._load_source(initializer, context)
        elif self._eligible(module_file):
            context = _ModuleContext(root, tuple(package), tuple(module))
            source = self._load_source(module_file, context)
        elif self._eligible_namespace(current / leaf):
            package.append(leaf)
            context = _ModuleContext(root, tuple(package), tuple(module))
            target = _NamespaceTarget(context)
            self._module_targets[(root, context.module)] = target
            result.append(target)
            return tuple(result)
        else:
            return None
        result.append(_ModuleTarget(source.path, context))
        return tuple(result)

    def _eligible_namespace(self, path: Path) -> bool:
        try:
            resolved = Path(os.path.abspath(path)).resolve(strict=True)
        except OSError:
            return False
        return resolved.is_dir() and self._inside_project(resolved)

    def _eligible(self, path: Path) -> bool:
        logical = Path(os.path.abspath(path))
        try:
            resolved = logical.resolve(strict=True)
            observation = resolved.stat()
        except OSError:
            return False
        return (
            resolved.suffix == ".py"
            and stat.S_ISREG(observation.st_mode)
            and self._inside_project(resolved)
        )

    def _module_member(
        self, module: _ModuleTarget | _NamespaceTarget, name: str
    ) -> _Binding | None:
        if isinstance(module, _NamespaceTarget):
            return None
        source = self._sources[module.path]
        return source.module_names.get(name)

    def resolve_attribute(self, scope: _Scope, node: ast.Attribute) -> _Binding | None:
        owner = _infer_binding(scope, node.value)
        if isinstance(owner, (_ModuleTarget, _NamespaceTarget)):
            child = self._module_targets.get(
                (owner.context.root, (*owner.context.module, node.attr))
            )
            if child is not None:
                return child
            return self._module_member(owner, node.attr)
        if isinstance(owner, _ClassInstance):
            return self._class_member(owner.definition, node.attr)
        if isinstance(owner, _DefinitionTarget) and self._is_class(owner):
            return self._class_member(owner, node.attr)
        if isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"}:
            if scope.owner_class is not None:
                return self._class_member(scope.owner_class, node.attr)
        return None

    def _class_member(self, target: _DefinitionTarget, name: str) -> _Binding | None:
        direct = _DefinitionTarget(target.path, (*target.qualname, name))
        source = self._sources[target.path]
        if direct.qualname in source.definitions:
            return direct
        node = source.definitions.get(target.qualname)
        if not isinstance(node, ast.ClassDef):
            return None
        for resolved in self._class_base_bindings(target, node):
            if isinstance(resolved, _DefinitionTarget) and self._is_class(resolved):
                inherited = self._class_member(resolved, name)
                if inherited is not None:
                    return inherited
        return None

    def remember_class_bases(
        self,
        target: _DefinitionTarget,
        node: ast.ClassDef,
        scope: _Scope,
    ) -> None:
        self._class_bases[target] = tuple(
            _resolve_expr(scope, base) for base in node.bases
        )

    def _class_base_bindings(
        self,
        target: _DefinitionTarget,
        node: ast.ClassDef,
    ) -> tuple[_Binding | None, ...]:
        known = self._class_bases.get(target)
        if known is not None:
            return known
        source = self._sources[target.path]
        module_scope = _Scope(source, self, names=source.module_names)
        return tuple(_resolve_expr(module_scope, base) for base in node.bases)

    def class_dispatch_reaches_external_base(self, binding: _Binding | None) -> bool:
        """Return whether unresolved class dispatch may terminate externally."""

        if isinstance(binding, _ClassInstance):
            target = binding.definition
        elif isinstance(binding, _DefinitionTarget) and self._is_class(binding):
            target = binding
        else:
            return False
        reaches_external, has_unknown_base = self._class_base_status(
            target,
            ancestors=frozenset(),
        )
        return reaches_external and not has_unknown_base

    def _class_base_status(
        self,
        target: _DefinitionTarget,
        *,
        ancestors: frozenset[_DefinitionTarget],
    ) -> tuple[bool, bool]:
        if target in ancestors:
            return False, True
        source = self._sources[target.path]
        node = source.definitions.get(target.qualname)
        if not isinstance(node, ast.ClassDef):
            return False, True
        next_ancestors = ancestors | {target}
        reaches_external = False
        has_unknown_base = False
        for resolved in self._class_base_bindings(target, node):
            if isinstance(resolved, _External):
                reaches_external = True
            elif isinstance(resolved, _DefinitionTarget) and self._is_class(resolved):
                nested_external, nested_unknown = self._class_base_status(
                    resolved,
                    ancestors=next_ancestors,
                )
                reaches_external = reaches_external or nested_external
                has_unknown_base = has_unknown_base or nested_unknown
            else:
                has_unknown_base = True
        return reaches_external, has_unknown_base

    def mark_definition(
        self, target: _DefinitionTarget, *, closure: _Scope | None = None
    ) -> None:
        if target in self._reached or target in self._analyzing:
            return
        source = self._sources[target.path]
        node = source.definitions.get(target.qualname)
        if node is None:
            return
        self._reached.add(target)
        if isinstance(node, ast.ClassDef):
            initializer = self._class_member(target, "__init__")
            if isinstance(initializer, _DefinitionTarget):
                self.mark_definition(initializer, closure=closure)
            return
        self._analyzing.add(target)
        try:
            parent = closure or _Scope(source, self, names=source.module_names)
            owner = (
                _DefinitionTarget(target.path, target.qualname[:-1])
                if len(target.qualname) > 1
                and isinstance(
                    source.definitions.get(target.qualname[:-1]), ast.ClassDef
                )
                else None
            )
            names = self._index_scope(source, node.body, target.qualname)
            for argument in _arguments(node.args):
                names[argument.arg] = _Unknown(argument.arg)
            if owner is not None:
                first = next(iter(_arguments(node.args)), None)
                if first is not None and first.arg in {"self", "cls"}:
                    names[first.arg] = _ClassInstance(owner)
            scope = _Scope(source, self, parent=parent, owner_class=owner, names=names)
            visitor = _Visitor(scope)
            for statement in node.body:
                visitor.visit(statement)
        finally:
            self._analyzing.remove(target)

    def is_project_owner(self, binding: _Binding | None) -> bool:
        return isinstance(
            binding, (_ModuleTarget, _NamespaceTarget, _ClassInstance)
        ) or (isinstance(binding, _DefinitionTarget) and self._is_class(binding))

    def _is_class(self, target: _DefinitionTarget) -> bool:
        return isinstance(
            self._sources[target.path].definitions.get(target.qualname), ast.ClassDef
        )

    def unsupported(
        self,
        source: _Source,
        node: ast.AST,
        construct: str,
        detail: str,
    ) -> None:
        self._unsupported.add(
            UnsupportedLocation(
                source.relative,
                getattr(node, "lineno", 0) or 0,
                construct,
                detail,
            )
        )

    def follow_child(self, scope: _Scope, node: ast.Call) -> None:
        command = _static_child_command(scope, node)
        if command is None:
            return
        child = command[1]
        bases: list[Path] = []
        current = scope.source.path.parent
        while current == self._project_root or self._project_root in current.parents:
            bases.append(current)
            if current == self._project_root:
                break
            current = current.parent
        candidates = _unique_paths(
            tuple(base / child for base in bases)
        )
        for candidate in candidates:
            if self._eligible(candidate):
                source = self._load_source(
                    candidate,
                    _ModuleContext(candidate.parent, (), ()),
                )
                self._activate(source)
                return
        self.unsupported(
            scope.source,
            node,
            "child_entrypoint_unresolved",
            f"Python child entrypoint {child!r} is not a resolvable project-local file",
        )

    def _fingerprint_payload(self) -> bytes:
        modules = []
        for source in sorted(self._sources.values(), key=lambda item: item.relative):
            if not source.analyzed:
                continue
            projection = _Projection(source.path, self._reached).visit(
                copy.deepcopy(source.tree)
            )
            ast.fix_missing_locations(projection)
            modules.append(
                {
                    "path": source.relative,
                    "syntax": ast.dump(
                        projection,
                        annotate_fields=True,
                        include_attributes=False,
                    ),
                }
            )
        value = {"algorithm": FINGERPRINT_ALGORITHM, "modules": modules}
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def _inside_project(self, path: Path) -> bool:
        try:
            path.relative_to(self._project_root)
        except ValueError:
            return False
        return True

    def _display(self, path: Path) -> str:
        try:
            return path.resolve(strict=False).relative_to(self._project_root).as_posix()
        except ValueError:
            return path.as_posix()


class _Visitor(ast.NodeVisitor):
    def __init__(self, scope: _Scope) -> None:
        self.scope = scope
        self.analyzer = scope.analyzer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition_header(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_definition_header(node)

    def _visit_definition_header(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for argument in _arguments(node.args):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        target = self.scope.resolve(node.name)
        owner = target if isinstance(target, _DefinitionTarget) else None
        if owner is not None:
            self.analyzer.remember_class_bases(owner, node, self.scope)
        for value in (*node.decorator_list, *node.bases):
            self.visit(value)
        for keyword in node.keywords:
            self.visit(keyword.value)
        names = (
            self.analyzer._index_scope(
                self.scope.source,
                node.body,
                owner.qualname if owner else (node.name,),
            )
            if owner
            else {}
        )
        class_scope = _Scope(
            self.scope.source,
            self.analyzer,
            parent=self.scope,
            owner_class=owner,
            names=names,
        )
        visitor = _Visitor(class_scope)
        for statement in node.body:
            visitor.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        self.scope.names.update(self.analyzer.resolve_import(self.scope.source, node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.scope.names.update(self.analyzer.resolve_import(self.scope.source, node))

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_path_mutation(node)
        self.visit(node.value)
        binding = _infer_binding(self.scope, node.value)
        for target in node.targets:
            self._bind_target(target, binding)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_path_mutation(node)
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            binding = _infer_binding(self.scope, node.value)
        else:
            binding = _Unknown()
        self._bind_target(node.target, binding)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, _infer_binding(self.scope, node.value))

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node.iter, node.target, node.body, node.orelse)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node.iter, node.target, node.body, node.orelse)

    def _visit_for(
        self,
        iterator: ast.expr,
        target: ast.expr,
        body: list[ast.stmt],
        otherwise: list[ast.stmt],
    ) -> None:
        self.visit(iterator)
        self._bind_target(target, _Unknown())
        for statement in (*body, *otherwise):
            self.visit(statement)

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node.items, node.body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_with(node.items, node.body)

    def _visit_with(self, items: list[ast.withitem], body: list[ast.stmt]) -> None:
        for item in items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, _Unknown())
        for statement in body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load):
            return
        self._mark(self.scope.resolve(node.id))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._mark(self.analyzer.resolve_attribute(self.scope, node))
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        name = _semantic_name(self.scope, node.func)
        if name in {"__import__", "builtins.__import__", "importlib.import_module"}:
            self.analyzer.unsupported(
                self.scope.source,
                node,
                "dynamic_import",
                "dynamic imports are not statically fingerprinted",
            )
        elif name in {"eval", "exec", "builtins.eval", "builtins.exec"}:
            self.analyzer.unsupported(
                self.scope.source,
                node,
                "dynamic_code",
                f"{name.rsplit('.', 1)[-1]} code is not statically fingerprinted",
            )
        if name.startswith("sys.path."):
            self.analyzer.unsupported(
                self.scope.source,
                node,
                "import_path_mutation",
                "runtime import-path mutation changes local source resolution",
            )
        self.analyzer.follow_child(self.scope, node)
        binding = _resolve_expr(self.scope, node.func)
        if isinstance(binding, _DefinitionTarget):
            self.analyzer.mark_definition(binding, closure=self.scope)
        else:
            owner = _resolve_call_owner(self.scope, node.func)
            if (
                isinstance(node.func, ast.Attribute)
                and binding is None
                and self.analyzer.is_project_owner(owner)
                and not self.analyzer.class_dispatch_reaches_external_base(owner)
            ):
                self.analyzer.unsupported(
                    self.scope.source,
                    node,
                    "project_dispatch_unresolved",
                    "project-local attribute or method "
                    f"{node.func.attr!r} is unresolved",
                )
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self.scope.names.pop(name, None)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self.scope.names.pop(name, None)

    def _bind_target(self, target: ast.expr, binding: _Binding) -> None:
        if isinstance(target, ast.Name):
            self.scope.names[target.id] = binding
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element, _Unknown())

    def _mark(self, binding: _Binding | None) -> None:
        if isinstance(binding, _DefinitionTarget):
            self.analyzer.mark_definition(binding, closure=self.scope)

    def generic_visit(self, node: ast.AST) -> None:
        self._record_path_mutation(node)
        super().generic_visit(node)

    def _record_path_mutation(self, node: ast.AST) -> None:
        if not _mutates_sys_path(self.scope, node):
            return
        self.analyzer.unsupported(
            self.scope.source,
            node,
            "import_path_mutation",
            "runtime import-path mutation changes local source resolution",
        )


class _Projection(ast.NodeTransformer):
    def __init__(self, path: Path, reached: set[_DefinitionTarget]) -> None:
        self._path = path
        self._reached = reached
        self._prefix: tuple[str, ...] = ()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        target = _DefinitionTarget(self._path, (*self._prefix, node.name))
        previous = self._prefix
        self._prefix = target.qualname
        try:
            if target in self._reached:
                return self.generic_visit(node)
            node.body = [ast.Pass()]
            return self.generic_visit(node)
        finally:
            self._prefix = previous

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        previous = self._prefix
        self._prefix = (*self._prefix, node.name)
        try:
            return self.generic_visit(node)
        finally:
            self._prefix = previous


def _resolve_expr(scope: _Scope, node: ast.expr) -> _Binding | None:
    if isinstance(node, ast.Name):
        return scope.resolve(node.id)
    if isinstance(node, ast.Attribute):
        local = scope.analyzer.resolve_attribute(scope, node)
        if local is not None:
            return local
        external = _external_name(scope, node)
        return _External(external) if external is not None else None
    return None


def _resolve_call_owner(scope: _Scope, node: ast.expr) -> _Binding | None:
    if isinstance(node, ast.Attribute):
        return _infer_binding(scope, node.value)
    return None


def _infer_binding(scope: _Scope, node: ast.expr) -> _Binding:
    resolved = _resolve_expr(scope, node)
    if resolved is not None:
        return resolved
    if isinstance(node, ast.Call):
        called = _resolve_expr(scope, node.func)
        if isinstance(called, _DefinitionTarget) and scope.analyzer._is_class(called):
            return _ClassInstance(called)
    return _Unknown()


def _arguments(arguments: ast.arguments) -> tuple[ast.arg, ...]:
    values = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    if arguments.vararg is not None:
        values.append(arguments.vararg)
    if arguments.kwarg is not None:
        values.append(arguments.kwarg)
    return tuple(values)


def _assigned_names(node: ast.AST) -> tuple[str, ...]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets.extend(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets.append(node.target)
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets.append(node.target)
    result: list[str] = []
    for target in targets:
        for candidate in ast.walk(target):
            if isinstance(candidate, ast.Name):
                result.append(candidate.id)
    return tuple(result)


def _lexical_statements(statements: Iterable[ast.stmt]) -> Iterable[ast.stmt]:
    for statement in statements:
        yield statement
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for _, value in ast.iter_fields(statement):
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, ast.stmt) for item in value)
            ):
                yield from _lexical_statements(value)


def _static_child_command(scope: _Scope, node: ast.Call) -> tuple[str, str] | None:
    name = _semantic_name(scope, node.func)
    if not name.startswith("subprocess.") or not node.args:
        return None
    command = node.args[0]
    if not isinstance(command, (ast.List, ast.Tuple)) or len(command.elts) < 2:
        return None
    executable, child = command.elts[:2]
    executable_name = _semantic_name(scope, executable)
    executable_value = (
        executable.value
        if isinstance(executable, ast.Constant) and isinstance(executable.value, str)
        else None
    )
    constant_python = (
        executable_value is not None and "python" in Path(executable_value).name.lower()
    )
    if not constant_python and executable_name != "sys.executable":
        return None
    if isinstance(child, ast.Constant) and isinstance(child.value, str):
        child_path = child.value
    elif (
        isinstance(child, ast.Name)
        and child.id == "__file__"
        and scope.resolve("__file__") is None
    ):
        child_path = str(scope.source.path)
    else:
        child_path = "<dynamic>"
    return executable_name or str(executable_value), child_path


def _semantic_name(scope: _Scope, node: ast.expr) -> str:
    external = _external_name(scope, node)
    return external if external is not None else _expression_name(node)


def _external_name(scope: _Scope, node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        binding = scope.resolve(node.id)
        if isinstance(binding, _External):
            return binding.name
        if binding is None and node.id in {"__import__", "eval", "exec"}:
            return node.id
        return None
    if isinstance(node, ast.Attribute):
        prefix = _external_name(scope, node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _expression_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _expression_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _mutates_sys_path(scope: _Scope, node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        targets = tuple(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = (node.target,)
    elif isinstance(node, ast.Delete):
        targets = tuple(node.targets)
    else:
        return False
    return any(
        _semantic_name(scope, target).startswith("sys.path") for target in targets
    )


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for path in paths:
        logical = Path(os.path.abspath(path))
        if logical not in result:
            result.append(logical)
    return tuple(result)
