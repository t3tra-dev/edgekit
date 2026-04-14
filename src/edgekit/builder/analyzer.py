from __future__ import annotations

import ast
from pathlib import Path
from typing import Literal

from .ast_support import (
    call_name as _call_name,
)
from .ast_support import (
    dynamic_keep_roots as _dynamic_keep_roots,
)
from .ast_support import (
    is_docstring_expr as _is_docstring_expr,
)
from .ast_support import (
    is_dunder_main_guard as _is_dunder_main_guard,
)
from .ast_support import (
    is_type_checking_guard as _is_type_checking_guard,
)
from .ast_support import (
    resolve_dynamic_import_call as _resolve_dynamic_import_call,
)
from .ast_support import (
    resolve_relative_import as _resolve_relative_import,
)
from .ast_support import (
    static_truth_value as _static_truth_value,
)
from .common import (
    module_with_package_ancestors as _module_with_package_ancestors,
)
from .common import (
    relative_path_from_root as _relative_path,
)
from .models import AnalysisResult, ModuleGraph, ModuleNode, ResolvedEnvironment, RiskReport

_IGNORED_DIRS = {
    ".cache",
    ".edgekit",
    ".git",
    ".uv-cache",
    ".venv",
    ".venv-workers",
    "__pycache__",
    "build",
    "node_modules",
    "python_modules",
    ".wrangler",
}


def analyze_project(environment: ResolvedEnvironment) -> AnalysisResult:
    module_paths = _discover_module_paths(environment.project_root)
    known_modules = frozenset(module_paths)
    entry_module = _path_to_module_name(environment.entry, environment.project_root)
    risks = RiskReport()
    nodes: dict[str, ModuleNode] = {}
    profile_side_effect_free_modules = _profile_side_effect_free_modules(environment)

    for module_name, path in module_paths.items():
        node = _analyze_module(
            module_name,
            path,
            environment.project_root,
            risks,
            known_modules=known_modules,
            side_effect_free_modules=profile_side_effect_free_modules,
        )
        nodes[module_name] = node

    if entry_module not in nodes:
        raise ValueError(f"Entrypoint module '{entry_module}' was not discovered")

    reachable, reasons = _compute_reachable(
        entry_module,
        nodes,
        mode=environment.config.mode,
        include_modules=environment.config.include.modules,
        profile_keep_modules=_profile_keep_modules(environment),
    )
    graph = ModuleGraph(entry_module=entry_module, nodes=nodes, reachable=reachable, reasons=reasons)
    return AnalysisResult(graph=graph, risks=risks)


def _discover_module_paths(project_root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}

    for path in project_root.rglob("*.py"):
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        module_name = _path_to_module_name(path, project_root)
        paths[module_name] = path

    return paths


def _path_to_module_name(path: Path, project_root: Path) -> str:
    path = path.resolve()
    src_root = (project_root / "src").resolve()

    if path.is_relative_to(src_root):
        relative = path.relative_to(src_root)
    else:
        relative = path.relative_to(project_root)

    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _analyze_module(
    module_name: str,
    path: Path,
    project_root: Path,
    risks: RiskReport,
    *,
    known_modules: frozenset[str],
    side_effect_free_modules: set[str],
) -> ModuleNode:
    tree = ast.parse(path.read_text(), filename=str(path))
    package_name = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    collector = _ImportCollector(module_name, package_name, known_modules=known_modules)
    collector.visit(tree)
    if module_name in side_effect_free_modules:
        collector.has_side_effect_risk = False

    if collector.has_unknown_dynamic_import:
        risks.add(
            "warning",
            "Dynamic import could not be resolved statically",
            path=_relative_path(path, project_root),
            code="dynamic_import",
        )
    if collector.has_side_effect_risk:
        risks.add(
            "info",
            "Top-level side-effect risk detected",
            path=_relative_path(path, project_root),
            code="top_level_side_effect",
        )

    return ModuleNode(
        name=module_name,
        path=path,
        imports=collector.imports,
        dynamic_imports=collector.dynamic_imports,
        type_checking_imports=collector.type_checking_imports,
        dynamic_keep_roots=collector.dynamic_keep_roots,
        has_unknown_dynamic_import=collector.has_unknown_dynamic_import,
        has_side_effect_risk=collector.has_side_effect_risk,
        is_package=path.name == "__init__.py",
        is_reexport_only=_is_reexport_only_module(tree),
    )


def _compute_reachable(
    entry_module: str,
    nodes: dict[str, ModuleNode],
    *,
    mode: Literal["safe", "aggressive"],
    include_modules: tuple[str, ...],
    profile_keep_modules: tuple[str, ...],
) -> tuple[set[str], dict[str, str]]:
    reachable: set[str] = set()
    reasons: dict[str, str] = {}
    pending: list[str] = []

    _enqueue_reachable(entry_module, nodes, reachable, pending, reasons, reason="entrypoint")
    for module_name in include_modules:
        _enqueue_reachable(module_name, nodes, reachable, pending, reasons, reason="included_by_config")
    for module_name in profile_keep_modules:
        _enqueue_reachable(module_name, nodes, reachable, pending, reasons, reason="kept_by_profile")

    while pending:
        current = pending.pop()
        node = nodes.get(current)
        if node is None:
            continue
        for imported in sorted(node.imports):
            _enqueue_reachable(imported, nodes, reachable, pending, reasons, reason="import")
        for imported in sorted(node.dynamic_imports):
            _enqueue_reachable(imported, nodes, reachable, pending, reasons, reason="dynamic_import")
        if mode == "safe" and node.has_unknown_dynamic_import:
            for prefix in sorted(node.dynamic_keep_roots):
                _enqueue_reachable_prefix(
                    prefix,
                    nodes,
                    reachable,
                    pending,
                    reasons,
                    reason="dynamic_import_conservative",
                )

    return reachable, reasons


def _enqueue_reachable(
    module_name: str,
    nodes: dict[str, ModuleNode],
    reachable: set[str],
    pending: list[str],
    reasons: dict[str, str],
    *,
    reason: str,
) -> None:
    for candidate in _module_with_package_ancestors(module_name):
        if candidate in nodes and candidate not in reachable:
            reachable.add(candidate)
            pending.append(candidate)
            reasons[candidate] = reason


def _enqueue_reachable_prefix(
    module_prefix: str,
    nodes: dict[str, ModuleNode],
    reachable: set[str],
    pending: list[str],
    reasons: dict[str, str],
    *,
    reason: str,
) -> None:
    for module_name in sorted(nodes):
        if module_name == module_prefix or module_name.startswith(f"{module_prefix}."):
            _enqueue_reachable(module_name, nodes, reachable, pending, reasons, reason=reason)


def _profile_keep_modules(environment: ResolvedEnvironment) -> tuple[str, ...]:
    modules = {module_name for profile in environment.package_profiles for module_name in profile.keep_modules}
    return tuple(sorted(modules))


def _profile_side_effect_free_modules(environment: ResolvedEnvironment) -> set[str]:
    return {module_name for profile in environment.package_profiles for module_name in profile.side_effect_free_modules}


class _ImportCollector(ast.NodeVisitor):
    def __init__(
        self,
        module_name: str,
        package_name: str,
        *,
        known_modules: frozenset[str],
    ) -> None:
        self._module_name = module_name
        self._package_name = package_name
        self._known_modules = known_modules
        self._parents: dict[int, ast.AST] = {}
        self._type_checking_depth = 0
        self.imports: set[str] = set()
        self.dynamic_imports: set[str] = set()
        self.type_checking_imports: set[str] = set()
        self.dynamic_keep_roots: set[str] = set()
        self.has_unknown_dynamic_import = False
        self.has_side_effect_risk = False

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return

        static_truth = _static_truth_value(node.test)
        if static_truth is True:
            for child in node.body:
                self.visit(child)
            return
        if static_truth is False:
            for child in node.orelse:
                self.visit(child)
            return
        if _is_import_only_block(node.body) and _is_import_only_block(node.orelse):
            for child in node.body:
                self.visit(child)
            for child in node.orelse:
                self.visit(child)
            return
        if self._is_top_level(node) and not _is_dunder_main_guard(node.test):
            self.has_side_effect_risk = True
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        if _is_import_only_try(node):
            for child in node.body:
                self.visit(child)
            for handler in node.handlers:
                for child in handler.body:
                    self.visit(child)
            for child in node.orelse:
                self.visit(child)
            for child in node.finalbody:
                self.visit(child)
            return
        if self._is_top_level(node):
            self.has_side_effect_risk = True
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if self._type_checking_depth:
                self.type_checking_imports.add(alias.name)
            else:
                self.imports.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = _resolve_relative_import(self._package_name, node.module, node.level)
        if not module_name:
            return
        target = self.type_checking_imports if self._type_checking_depth else self.imports
        if any(alias.name == "*" for alias in node.names):
            target.add(module_name)
            return
        for alias in node.names:
            candidate_module = f"{module_name}.{alias.name}" if module_name else alias.name
            if candidate_module in self._known_modules:
                target.add(candidate_module)
            else:
                target.add(module_name)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        if name in {"__import__", "importlib.import_module"}:
            resolved_import = _resolve_dynamic_import_call(node, current_package=self._package_name)
            if resolved_import is None:
                self.has_unknown_dynamic_import = True
                self.dynamic_keep_roots.update(
                    _dynamic_keep_roots(node, module_name=self._module_name, package_name=self._package_name)
                )
            elif resolved_import:
                self.dynamic_imports.add(resolved_import)
        self.generic_visit(node)

    def visit_Module(self, node: ast.Module) -> None:
        for child in node.body:
            self._set_parent(child, node)
            self.visit(child)

    def generic_visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if id(child) not in self._parents:
                self._set_parent(child, node)
        super().generic_visit(node)
        parent = self._parent_of(node)
        if isinstance(parent, ast.Module) and _is_side_effect_statement(node):
            self.has_side_effect_risk = True

    def _set_parent(self, node: ast.AST, parent: ast.AST) -> None:
        self._parents[id(node)] = parent

    def _parent_of(self, node: ast.AST) -> ast.AST | None:
        return self._parents.get(id(node))

    def _is_top_level(self, node: ast.AST) -> bool:
        return isinstance(self._parent_of(node), ast.Module)


def _is_import_only_block(statements: list[ast.stmt]) -> bool:
    return all(_is_import_only_statement(statement) for statement in statements)


def _is_import_only_statement(node: ast.stmt) -> bool:
    if isinstance(node, (ast.Import, ast.ImportFrom, ast.Pass)):
        return True
    if _is_docstring_expr(node) or _is_dunder_all_assignment(node):
        return True
    if isinstance(node, ast.If):
        return _is_import_only_block(node.body) and _is_import_only_block(node.orelse)
    if isinstance(node, ast.Try):
        return _is_import_only_try(node)
    return False


def _is_import_only_try(node: ast.Try) -> bool:
    return (
        _is_import_only_block(node.body)
        and all(_is_import_only_block(handler.body) for handler in node.handlers)
        and _is_import_only_block(node.orelse)
        and _is_import_only_block(node.finalbody)
    )


def _is_dunder_all_assignment(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        return any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and node.target.id == "__all__"
    return False


def _is_reexport_only_module(tree: ast.Module) -> bool:
    has_import = False
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            has_import = True
            continue
        if _is_docstring_expr(statement) or _is_dunder_all_assignment(statement) or isinstance(statement, ast.Pass):
            continue
        return False
    return has_import


def _is_side_effect_statement(node: ast.AST) -> bool:
    if isinstance(node, ast.If) and _is_dunder_main_guard(node.test):
        return False
    if _is_docstring_expr(node) or _is_dunder_all_assignment(node):
        return False
    safe_nodes = (
        ast.Import,
        ast.ImportFrom,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Assign,
        ast.AnnAssign,
        ast.TypeAlias,
        ast.Try,
        ast.Pass,
    )
    return not isinstance(node, safe_nodes)
