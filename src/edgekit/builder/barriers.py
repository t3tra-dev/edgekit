from __future__ import annotations

import ast
from dataclasses import dataclass
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Literal

from .ast_support import (
    VendorImportCollector as _VendorImportCollector,
)
from .ast_support import (
    call_name as _call_name,
)
from .ast_support import (
    constant_string_argument as _constant_string_argument,
)
from .ast_support import (
    is_class_member_lookup_target as _is_class_member_lookup_target,
)
from .ast_support import (
    positional_argument as _positional_argument,
)
from .common import (
    enqueue_module_with_ancestors as _enqueue_module_with_ancestors,
)
from .common import (
    module_name_from_relative_path as _module_name_from_relative_path,
)
from .common import (
    module_package_name as _vendor_package_name,
)
from .common import (
    normalize_package_name as _normalize_distribution_name,
)
from .models import AnalysisResult, ResolvedEnvironment, RiskReport, RuntimeStatus
from .vendor_support import (
    VendorModuleSource as _VendorModuleSource,
)
from .vendor_support import (
    editable_source_roots as _editable_source_roots,
)
from .vendor_support import (
    is_editable_runtime_path as _is_editable_runtime_path,
)
from .vendor_support import (
    parse_requirement_name as _parse_requirement_name,
)

_VENDOR_IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyi"}
_ATTRIBUTE_PROTOCOL_HOOKS = {"__getattr__", "__getattribute__", "__setattr__", "__delattr__", "__dir__"}


@dataclass(slots=True, frozen=True)
class _BarrierFinding:
    scope: str
    kinds: tuple[str, ...]


@dataclass(slots=True)
class _VendorImportNode:
    imports: set[str]
    dynamic_imports: set[str]


def collect_symbol_pruning_barrier_risks(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    *,
    externalized_distributions: frozenset[str] = frozenset(),
) -> RiskReport:
    report = RiskReport()
    level: Literal["info", "warning", "error"] = "warning" if environment.config.mode == "aggressive" else "info"

    for module_name in sorted(analysis.graph.reachable):
        node = analysis.graph.nodes.get(module_name)
        if node is None:
            continue
        findings = _collect_barrier_findings(node.path.read_text())
        for finding in findings:
            report.add(
                level,
                _project_barrier_message(finding),
                path=str(node.path.relative_to(environment.project_root)),
                code="symbol_pruning_barrier",
            )

    for vendor_module in _reachable_vendor_modules(
        environment,
        analysis,
        externalized_distributions=externalized_distributions,
    ):
        findings = _collect_barrier_findings(vendor_module.source)
        for finding in findings:
            report.add(
                level,
                _vendor_barrier_message(vendor_module.distribution, finding),
                path=f"python_modules/{vendor_module.relative_path.as_posix()}",
                code="symbol_pruning_barrier",
            )

    return report


def source_has_symbol_pruning_barrier(source: str) -> bool:
    return bool(_collect_barrier_findings(source))


def symbol_pruning_barrier_scopes(source: str) -> frozenset[str]:
    return frozenset(finding.scope for finding in _collect_barrier_findings(source))


def _project_barrier_message(finding: _BarrierFinding) -> str:
    kinds = ", ".join(finding.kinds)
    return f"Aggressive symbol pruning barrier in {finding.scope}: {kinds}"


def _vendor_barrier_message(distribution_name: str, finding: _BarrierFinding) -> str:
    kinds = ", ".join(finding.kinds)
    return f"Aggressive symbol pruning barrier in {finding.scope} ({distribution_name}): {kinds}"


def _reachable_vendor_modules(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    *,
    externalized_distributions: frozenset[str] = frozenset(),
) -> tuple[_VendorModuleSource, ...]:
    vendor_sources = _collect_barrier_vendor_module_sources(
        environment,
        externalized_distributions=externalized_distributions,
    )
    if not vendor_sources:
        return ()

    project_modules = set(analysis.graph.nodes)
    root_modules: set[str] = set()
    for module_name in analysis.graph.reachable:
        node = analysis.graph.nodes.get(module_name)
        if node is None:
            continue
        root_modules.update(imported for imported in node.imports if imported not in project_modules)
        root_modules.update(imported for imported in node.dynamic_imports if imported not in project_modules)
    for profile in environment.package_profiles:
        root_modules.update(profile.keep_modules)

    known_vendor_modules = frozenset(vendor_sources)
    vendor_nodes = {
        module_name: _scan_vendor_imports(module_source.source, module_name, known_vendor_modules=known_vendor_modules)
        for module_name, module_source in vendor_sources.items()
    }

    reachable: set[str] = set()
    pending: list[str] = []
    for module_name in sorted(root_modules):
        _enqueue_module_with_ancestors(module_name, known_vendor_modules, reachable, pending)

    while pending:
        current = pending.pop()
        node = vendor_nodes.get(current)
        if node is None:
            continue
        for imported in sorted(node.imports):
            _enqueue_module_with_ancestors(imported, known_vendor_modules, reachable, pending)
        for imported in sorted(node.dynamic_imports):
            _enqueue_module_with_ancestors(imported, known_vendor_modules, reachable, pending)

    return tuple(vendor_sources[module_name] for module_name in sorted(reachable))


def _collect_barrier_vendor_module_sources(
    environment: ResolvedEnvironment,
    *,
    externalized_distributions: frozenset[str] = frozenset(),
) -> dict[str, _VendorModuleSource]:
    modules: dict[str, _VendorModuleSource] = {}
    distribution_names = _iter_distribution_names(
        environment,
        externalized_distributions=externalized_distributions,
    )

    for distribution_name in distribution_names:
        dist = distribution(distribution_name)
        for relative_path, source_path in _iter_distribution_python_files(dist):
            module_name = _module_name_from_relative_path(relative_path)
            if module_name is None:
                continue
            modules.setdefault(
                module_name,
                _VendorModuleSource(
                    name=module_name,
                    distribution=distribution_name,
                    source_path=source_path,
                    relative_path=relative_path,
                    source=source_path.read_text(),
                ),
            )

    return modules


def _iter_distribution_names(
    environment: ResolvedEnvironment,
    *,
    externalized_distributions: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    pending = sorted(
        _normalize_distribution_name(package.name)
        for package in environment.dependencies
        if package.runtime_status != RuntimeStatus.EXTERNAL_RUNTIME
        and _normalize_distribution_name(package.name) not in externalized_distributions
    )
    direct = set(pending)
    seen: set[str] = set()
    ordered: list[str] = []

    while pending:
        candidate = pending.pop(0)
        if candidate in seen:
            continue
        try:
            dist = distribution(candidate)
        except PackageNotFoundError:
            if candidate in direct:
                raise RuntimeError(f"Installed distribution not found for dependency: {candidate}")
            continue

        canonical_name = _normalize_distribution_name(dist.metadata.get("Name", candidate))
        if canonical_name in externalized_distributions:
            continue
        if canonical_name in seen:
            continue
        seen.add(canonical_name)
        ordered.append(canonical_name)

        for requirement in sorted(dist.requires or ()):
            requirement_name = _parse_requirement_name(requirement)
            if requirement_name is not None:
                pending.append(requirement_name)

    return tuple(ordered)


def _iter_distribution_python_files(dist: Distribution) -> tuple[tuple[Path, Path], ...]:
    dist_files = dist.files
    if dist_files is None:
        files: list[tuple[Path, Path]] = []
    else:
        site_packages_root = Path(str(dist.locate_file(""))).resolve()
        files = []
        for file in sorted(dist_files, key=lambda item: str(item)):
            source_path = Path(str(dist.locate_file(file))).resolve()
            if source_path.is_dir():
                continue
            try:
                relative_path = source_path.relative_to(site_packages_root)
            except ValueError:
                continue
            if not _is_vendor_python_file(relative_path):
                continue
            files.append((relative_path, source_path))

    for source_root in _editable_source_roots(dist):
        for source_path in sorted(source_root.rglob("*.py")):
            if source_path.is_dir():
                continue
            try:
                relative_path = source_path.relative_to(source_root)
            except ValueError:
                continue
            if not _is_editable_runtime_path(source_root, relative_path):
                continue
            files.append((relative_path, source_path.resolve()))

    deduped: dict[str, tuple[Path, Path]] = {}
    for relative_path, source_path in files:
        deduped.setdefault(relative_path.as_posix(), (relative_path, source_path))
    return tuple(deduped[key] for key in sorted(deduped))


def _is_vendor_python_file(relative_path: Path) -> bool:
    if relative_path.suffix != ".py":
        return False
    if relative_path.suffix in _VENDOR_IGNORED_SUFFIXES:
        return False
    if "__pycache__" in relative_path.parts:
        return False
    return True


def _scan_vendor_imports(
    source: str,
    module_name: str,
    *,
    known_vendor_modules: frozenset[str],
) -> _VendorImportNode:
    collector = _VendorImportCollector(
        module_name,
        package_name=_vendor_package_name(module_name),
        known_modules=known_vendor_modules,
    )
    collector.visit(ast.parse(source, filename=module_name))
    return _VendorImportNode(imports=collector.imports, dynamic_imports=collector.dynamic_imports)


def _collect_barrier_findings(source: str) -> tuple[_BarrierFinding, ...]:
    collector = _BarrierCollector()
    collector.visit(ast.parse(source))
    return tuple(
        _BarrierFinding(scope=scope, kinds=tuple(sorted(kinds))) for scope, kinds in sorted(collector.findings.items())
    )


class _BarrierCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.class_stack: list[str] = []
        self.findings: dict[str, set[str]] = {}

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.class_stack and node.name in _ATTRIBUTE_PROTOCOL_HOOKS:
            self._add_barrier("attribute_protocol_hook")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.class_stack and node.name in _ATTRIBUTE_PROTOCOL_HOOKS:
            self._add_barrier("attribute_protocol_hook")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self.class_stack
            and node.attr == "__dict__"
            and _is_class_member_lookup_target(node.value, class_name=self.class_stack[-1])
        ):
            self._add_barrier("instance_dict_lookup")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        current_class = self.class_stack[-1] if self.class_stack else None

        if current_class is not None:
            if name in {"getattr", "hasattr", "setattr", "delattr"} and _is_class_member_lookup_target(
                _positional_argument(node, 0),
                class_name=current_class,
            ):
                if _constant_string_argument(node, 1) is None:
                    self._add_barrier("dynamic_attribute_lookup")
            if name in {"vars", "dir", "inspect.getmembers"} and _is_class_member_lookup_target(
                _positional_argument(node, 0),
                class_name=current_class,
            ):
                self._add_barrier("reflective_member_lookup")

        if name in {"globals", "locals"}:
            self._add_barrier("dynamic_namespace_lookup")

        self.generic_visit(node)

    def _add_barrier(self, kind: str) -> None:
        scope = self.class_stack[-1] if self.class_stack else "module"
        self.findings.setdefault(scope, set()).add(kind)
