# pyright: reportPrivateUsage=false
from __future__ import annotations

import shutil
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from ..common import normalize_package_name as _normalize_distribution_name
from ..config import PackageProfile
from ..models import AnalysisResult, PrunedDistribution, ResolvedEnvironment
from ..vendor_support import (
    editable_source_roots as _editable_source_roots,
)
from ..vendor_support import (
    is_editable_runtime_path as _is_editable_runtime_path,
)
from ..vendor_support import (
    parse_requirement_name as _parse_requirement_name,
)
from ..vendor_support import (
    replace_vendor_module_sources as _replace_vendor_module_sources,
)
from .symbols import (
    _build_symbol_sliced_vendor_sources,
    _collect_protected_method_names,
    _collect_vendor_nodes,
    _compute_reachable_vendor_modules,
    _module_name_from_relative_path,
    _vendor_root_modules,
)
from .transform import (
    _compact_python_source_text,
    _remove_unused_imports_after_strip,
    _strip_comments_from_source,
    _strip_docstrings_from_source,
    _strip_instance_methods_from_source,
)
from .types import (
    _DistributionSourceFile,
    _VendorModuleSource,
    _VendorPruningIndex,
)


def emit_vendor_modules(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    pruned: PrunedDistribution,
    vendor_root: Path,
) -> None:
    vendor_root.mkdir(parents=True, exist_ok=True)

    externalized_names = {_normalize_distribution_name(package.name) for package in pruned.externalized_packages}
    distribution_names = _iter_vendor_distribution_names(environment, externalized_names)
    source_files_by_distribution = {
        distribution_name: _distribution_source_files(distribution_name, environment)
        for distribution_name in distribution_names
    }
    pruning_index = _build_vendor_pruning_index(environment, analysis, source_files_by_distribution)
    for distribution_name in distribution_names:
        _copy_distribution_files(
            distribution_name,
            vendor_root,
            pruning_index,
            source_files=source_files_by_distribution[distribution_name],
        )

    (vendor_root / "pyvenv.cfg").touch()


def _iter_vendor_distribution_names(
    environment: ResolvedEnvironment,
    externalized_names: set[str],
) -> tuple[str, ...]:
    pending = sorted(
        _normalize_distribution_name(package.name)
        for package in environment.dependencies
        if _normalize_distribution_name(package.name) not in externalized_names
    )
    seen: set[str] = set()
    ordered: list[str] = []
    direct = set(pending)

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
        if canonical_name in externalized_names:
            continue
        if canonical_name in seen:
            continue

        seen.add(canonical_name)
        ordered.append(canonical_name)

        for requirement in sorted(dist.requires or ()):
            requirement_name = _parse_requirement_name(requirement)
            if requirement_name is not None and requirement_name not in externalized_names:
                pending.append(requirement_name)

    return tuple(ordered)


def _copy_distribution_files(
    distribution_name: str,
    vendor_root: Path,
    pruning_index: _VendorPruningIndex,
    *,
    source_files: tuple[_DistributionSourceFile, ...],
) -> None:
    for source_file in source_files:
        if not _should_copy_distribution_source_file(source_file, pruning_index):
            continue
        target_path = vendor_root / source_file.relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        module_name = pruning_index.module_paths_by_distribution.get(source_file.distribution, {}).get(
            source_file.relative_path.as_posix()
        )
        transformed = pruning_index.transformed_sources_by_module.get(module_name) if module_name is not None else None
        if transformed is None:
            shutil.copy2(source_file.source_path, target_path)
            continue
        target_path.write_text(_compact_python_source_text(transformed))


def _profile_for_distribution(
    package_profiles: tuple[PackageProfile, ...],
    distribution_name: str,
) -> PackageProfile | None:
    normalized_name = _normalize_distribution_name(distribution_name)
    for profile in package_profiles:
        if _normalize_distribution_name(profile.name) == normalized_name:
            return profile
    return None


def _should_copy_distribution_file(
    relative_path: Path,
    environment: ResolvedEnvironment,
    profile: PackageProfile | None,
) -> bool:
    if "__pycache__" in relative_path.parts:
        return False
    if relative_path.suffix in {".pth", ".pyc", ".pyo", ".pyi", ".c", ".h", ".so", ".pyd", ".dylib", ".dll"}:
        return False
    if relative_path.name == "py.typed":
        return False
    if environment.config.strip_metadata and any(
        part.endswith((".dist-info", ".egg-info")) for part in relative_path.parts
    ):
        return False
    if environment.config.strip_tests and _is_test_path(relative_path):
        return False
    if environment.config.strip_docs and _is_doc_path(relative_path):
        return False
    if environment.config.strip_examples and _is_example_path(relative_path):
        return False
    if profile is not None and _matches_profile_strip(relative_path, profile.strip):
        return False
    return True


def _matches_profile_strip(path: Path, strip_targets: Iterable[str]) -> bool:
    normalized_targets = {target.strip().lower() for target in strip_targets if target.strip()}
    if not normalized_targets:
        return False
    return any(part.lower() in normalized_targets for part in path.parts)


def _is_test_path(path: Path) -> bool:
    directory_parts = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    return (
        "tests" in directory_parts
        or "testing" in directory_parts
        or name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def _effective_method_patterns_for_source(
    relative_path: Path,
    method_patterns: tuple[str, ...],
) -> tuple[str, ...]:
    if not method_patterns:
        return ()
    if not _is_test_path(relative_path):
        return ()
    return method_patterns


def _is_doc_path(path: Path) -> bool:
    return "docs" in path.parts or path.suffix.lower() in {".md", ".rst"}


def _is_example_path(path: Path) -> bool:
    return "examples" in path.parts


def _distribution_source_files(
    distribution_name: str,
    environment: ResolvedEnvironment,
) -> tuple[_DistributionSourceFile, ...]:
    dist = distribution(distribution_name)
    site_packages_root = Path(str(dist.locate_file(""))).resolve()
    profile = _profile_for_distribution(environment.package_profiles, distribution_name)
    files: dict[str, _DistributionSourceFile] = {}

    for file in sorted(dist.files or (), key=lambda item: str(item)):
        source_path = Path(str(dist.locate_file(file))).resolve()
        if source_path.is_dir():
            continue
        try:
            relative_path = source_path.relative_to(site_packages_root)
        except ValueError:
            continue
        if not _should_copy_distribution_file(relative_path, environment, profile):
            continue
        files.setdefault(
            relative_path.as_posix(),
            _DistributionSourceFile(
                distribution=distribution_name, source_path=source_path, relative_path=relative_path
            ),
        )

    for source_root in _editable_source_roots(dist):
        for source_path in sorted(source_root.rglob("*")):
            if source_path.is_dir():
                continue
            try:
                relative_path = source_path.relative_to(source_root)
            except ValueError:
                continue
            if not _should_copy_distribution_file(relative_path, environment, profile):
                continue
            if not _is_editable_runtime_path(source_root, relative_path):
                continue
            files.setdefault(
                relative_path.as_posix(),
                _DistributionSourceFile(
                    distribution=distribution_name, source_path=source_path, relative_path=relative_path
                ),
            )

    return tuple(files[key] for key in sorted(files))


def _transformed_distribution_source(
    source_file: _DistributionSourceFile,
    environment: ResolvedEnvironment,
    *,
    protected_method_names: frozenset[str] = frozenset(),
) -> str | None:
    if source_file.relative_path.suffix != ".py":
        return None

    original = source_file.source_path.read_text()
    transformed = original
    if environment.config.strip_docs:
        transformed = _strip_docstrings_from_source(transformed)
        transformed = _strip_comments_from_source(transformed)
    effective_method_patterns = _effective_method_patterns_for_source(
        source_file.relative_path,
        environment.config.strip_methods,
    )
    stripped = _strip_instance_methods_from_source(
        transformed,
        method_patterns=effective_method_patterns,
        protected_method_names=protected_method_names,
    )
    return _remove_unused_imports_after_strip(transformed, stripped)


def _build_vendor_pruning_index(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    source_files_by_distribution: dict[str, tuple[_DistributionSourceFile, ...]],
) -> _VendorPruningIndex:
    vendor_root_modules = _vendor_root_modules(environment, analysis)
    protected_method_names: frozenset[str] = frozenset()
    module_sources_by_distribution = _collect_transformed_vendor_module_sources(
        environment,
        source_files_by_distribution,
        protected_method_names=protected_method_names,
    )
    vendor_nodes, module_paths_by_distribution = _collect_vendor_nodes(module_sources_by_distribution)
    if environment.config.strip_methods and any(
        _effective_method_patterns_for_source(source_file.relative_path, environment.config.strip_methods)
        for source_files in source_files_by_distribution.values()
        for source_file in source_files
    ):
        initially_reachable = _compute_reachable_vendor_modules(
            vendor_nodes,
            vendor_root_modules,
            mode=environment.config.mode,
        )
        current_vendor_sources = {
            module_source.name: module_source.source
            for module_sources in module_sources_by_distribution.values()
            for module_source in module_sources
        }
        protected_method_names = _collect_protected_method_names(
            analysis,
            current_vendor_sources,
            initially_reachable,
            method_patterns=environment.config.strip_methods,
        )
        module_sources_by_distribution = _collect_transformed_vendor_module_sources(
            environment,
            source_files_by_distribution,
            protected_method_names=protected_method_names,
        )
        vendor_nodes, module_paths_by_distribution = _collect_vendor_nodes(module_sources_by_distribution)

    transformed_sources_by_module = _build_symbol_sliced_vendor_sources(
        environment,
        analysis,
        module_sources_by_distribution,
        vendor_root_modules=vendor_root_modules,
    )
    final_module_sources_by_distribution = _replace_vendor_module_sources(
        module_sources_by_distribution,
        transformed_sources_by_module,
    )
    vendor_nodes, module_paths_by_distribution = _collect_vendor_nodes(final_module_sources_by_distribution)

    reachable_modules = _compute_reachable_vendor_modules(
        vendor_nodes,
        vendor_root_modules,
        mode=environment.config.mode,
    )
    reachable_roots_by_distribution: dict[str, set[str]] = {}
    for module_name in reachable_modules:
        node = vendor_nodes.get(module_name)
        if node is None or not node.relative_path.parts:
            continue
        reachable_roots_by_distribution.setdefault(node.distribution, set()).add(node.relative_path.parts[0])

    return _VendorPruningIndex(
        reachable_modules=reachable_modules,
        reachable_roots_by_distribution=reachable_roots_by_distribution,
        module_paths_by_distribution=module_paths_by_distribution,
        protected_method_names=protected_method_names,
        transformed_sources_by_module=transformed_sources_by_module,
    )


def _collect_transformed_vendor_module_sources(
    environment: ResolvedEnvironment,
    source_files_by_distribution: dict[str, tuple[_DistributionSourceFile, ...]],
    *,
    protected_method_names: frozenset[str],
) -> dict[str, tuple[_VendorModuleSource, ...]]:
    module_sources_by_distribution: dict[str, tuple[_VendorModuleSource, ...]] = {}

    for distribution_name, source_files in source_files_by_distribution.items():
        module_sources: list[_VendorModuleSource] = []
        for source_file in source_files:
            module_name = _module_name_from_relative_path(source_file.relative_path)
            if module_name is None:
                continue
            transformed_source = _transformed_distribution_source(
                source_file,
                environment,
                protected_method_names=protected_method_names,
            )
            if transformed_source is None:
                continue
            module_sources.append(
                _VendorModuleSource(
                    name=module_name,
                    distribution=distribution_name,
                    source_path=source_file.source_path,
                    relative_path=source_file.relative_path,
                    source=transformed_source,
                )
            )
        module_sources_by_distribution[distribution_name] = tuple(module_sources)

    return module_sources_by_distribution


def _should_copy_distribution_source_file(
    source_file: _DistributionSourceFile,
    pruning_index: _VendorPruningIndex,
) -> bool:
    relative_key = source_file.relative_path.as_posix()
    module_name = pruning_index.module_paths_by_distribution.get(source_file.distribution, {}).get(relative_key)
    if module_name is not None:
        return module_name in pruning_index.reachable_modules
    root_name = source_file.relative_path.parts[0] if source_file.relative_path.parts else ""
    if not root_name:
        return False
    return root_name in pruning_index.reachable_roots_by_distribution.get(source_file.distribution, set())
