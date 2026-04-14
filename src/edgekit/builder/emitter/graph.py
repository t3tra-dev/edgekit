# pyright: reportPrivateUsage=false, reportUnusedFunction=false
from __future__ import annotations

import ast

from ..ast_support import VendorImportCollector as _VendorImportCollector
from ..common import (
    enqueue_module_with_ancestors as _enqueue_module_with_ancestors,
)
from ..common import (
    module_name_from_relative_path,
)
from ..common import (
    module_package_name as _vendor_package_name,
)
from ..mode import BuildMode
from ..models import AnalysisResult, ResolvedEnvironment
from ..vendor_support import replace_vendor_module_sources
from .types import _VendorModuleNode, _VendorModuleSource

_module_name_from_relative_path = module_name_from_relative_path
_replace_vendor_module_sources = replace_vendor_module_sources


def _collect_vendor_nodes(
    module_sources_by_distribution: dict[str, tuple[_VendorModuleSource, ...]],
) -> tuple[dict[str, _VendorModuleNode], dict[str, dict[str, str]]]:
    vendor_nodes: dict[str, _VendorModuleNode] = {}
    module_paths_by_distribution: dict[str, dict[str, str]] = {}
    known_modules = frozenset(
        module_source.name
        for module_sources in module_sources_by_distribution.values()
        for module_source in module_sources
    )

    for distribution_name, module_sources in module_sources_by_distribution.items():
        module_paths: dict[str, str] = {}
        for module_source in module_sources:
            collector = _VendorImportCollector(
                module_source.name,
                package_name=_vendor_package_name(module_source.name, relative_path=module_source.relative_path),
                known_modules=known_modules,
            )
            collector.visit(ast.parse(module_source.source, filename=str(module_source.source_path)))
            vendor_nodes[module_source.name] = _VendorModuleNode(
                name=module_source.name,
                distribution=distribution_name,
                source_path=module_source.source_path,
                relative_path=module_source.relative_path,
                imports=collector.imports,
                dynamic_imports=collector.dynamic_imports,
                dynamic_keep_roots=collector.dynamic_keep_roots,
                has_unknown_dynamic_import=collector.has_unknown_dynamic_import,
            )
            module_paths[module_source.relative_path.as_posix()] = module_source.name
        module_paths_by_distribution[distribution_name] = module_paths

    return vendor_nodes, module_paths_by_distribution


def _vendor_root_modules(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
) -> tuple[str, ...]:
    project_module_names = set(analysis.graph.nodes)
    roots: set[str] = set()
    for module_name in analysis.graph.reachable:
        node = analysis.graph.nodes.get(module_name)
        if node is None:
            continue
        roots.update(imported for imported in node.imports if imported not in project_module_names)
        roots.update(imported for imported in node.dynamic_imports if imported not in project_module_names)
    for profile in environment.package_profiles:
        roots.update(profile.keep_modules)
    return tuple(sorted(roots))


def _compute_reachable_vendor_modules(
    vendor_nodes: dict[str, _VendorModuleNode],
    root_modules: tuple[str, ...],
    *,
    mode: BuildMode,
) -> set[str]:
    reachable: set[str] = set()
    pending: list[str] = []

    for module_name in root_modules:
        _enqueue_module_with_ancestors(module_name, vendor_nodes, reachable, pending)

    while pending:
        current = pending.pop()
        node = vendor_nodes.get(current)
        if node is None:
            continue
        for imported in sorted(node.imports):
            _enqueue_module_with_ancestors(imported, vendor_nodes, reachable, pending)
        for imported in sorted(node.dynamic_imports):
            _enqueue_module_with_ancestors(imported, vendor_nodes, reachable, pending)
        if mode == "safe" and node.has_unknown_dynamic_import:
            for prefix in sorted(node.dynamic_keep_roots):
                _enqueue_vendor_prefix(prefix, vendor_nodes, reachable, pending)

    return reachable


def _enqueue_vendor_prefix(
    module_prefix: str,
    vendor_nodes: dict[str, _VendorModuleNode],
    reachable: set[str],
    pending: list[str],
) -> None:
    for module_name in sorted(vendor_nodes):
        if module_name == module_prefix or module_name.startswith(f"{module_prefix}."):
            _enqueue_module_with_ancestors(module_name, vendor_nodes, reachable, pending)
