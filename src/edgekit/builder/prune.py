from __future__ import annotations

from pathlib import Path
from typing import Literal

from .common import relative_path_from_root as _relative_path
from .models import (
    AnalysisResult,
    PrunedDistribution,
    PruneDecision,
    ResolvedEnvironment,
    ResolvedPackage,
    RuntimeProvider,
    RuntimeStatus,
)

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


def prune_distribution(environment: ResolvedEnvironment, analysis: AnalysisResult) -> PrunedDistribution:
    decisions: dict[str, PruneDecision] = {}
    project_root = environment.project_root
    entry_relative = str(environment.entry.relative_to(project_root))
    externalized_packages: list[ResolvedPackage] = []

    def keep(path: Path, reason: str, *, kind: Literal["module", "file", "package"] = "file") -> None:
        relative = _relative_path(path, project_root)
        decisions[relative] = PruneDecision(target=relative, action="keep", reason=reason, kind=kind)

    def drop(path: Path, reason: str, *, kind: Literal["module", "file", "package"] = "file") -> None:
        relative = _relative_path(path, project_root)
        if relative == entry_relative:
            keep(path, "entrypoint", kind=kind)
            return
        decisions[relative] = PruneDecision(target=relative, action="drop", reason=reason, kind=kind)

    for package in _externalizable_packages(environment):
        if not _should_externalize(environment, package):
            continue
        decisions[package.name] = PruneDecision(
            target=package.name,
            action="externalize",
            reason=_externalize_reason(package),
            kind="package",
        )
        externalized_packages.append(package)

    exclude_modules = set(environment.config.exclude.modules)

    for module_name, node in analysis.graph.nodes.items():
        if module_name in exclude_modules:
            drop(node.path, "excluded_by_config", kind="module")
        elif module_name in analysis.graph.reachable:
            reason = analysis.graph.reasons.get(module_name, "reachable")
            keep(node.path, reason, kind="module")
        else:
            drop(node.path, "unreachable", kind="module")

    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue

        relative = _relative_path(path, project_root)
        if relative in decisions:
            continue

        if _matches_any(relative, environment.config.include.files):
            keep(path, "included_by_config")
            continue
        if _matches_any(relative, environment.config.exclude.files):
            drop(path, "excluded_by_config")
            continue
        if _is_metadata_file(path) and environment.config.strip_metadata:
            drop(path, "metadata")
            continue
        if _is_test_file(path) and environment.config.strip_tests:
            drop(path, "tests")
            continue
        if _is_doc_file(path) and environment.config.strip_docs:
            drop(path, "docs")
            continue
        if _is_example_file(path) and environment.config.strip_examples:
            drop(path, "examples")
            continue
        if path.name == "py.typed":
            keep(path, "typing_marker")
            continue
        if path.name == "pyproject.toml":
            keep(path, "build_metadata")
            continue
        if path.name in {"wrangler.jsonc", "wrangler.json", "wrangler.toml"}:
            drop(path, "wrangler_config")

    kept_files = [
        project_root / decision.target
        for decision in sorted(decisions.values(), key=lambda item: item.target)
        if decision.action == "keep" and decision.kind != "package"
    ]
    return PrunedDistribution(
        kept_files=kept_files,
        decisions=sorted(decisions.values(), key=lambda item: (item.kind, item.target)),
        externalized_packages=tuple(externalized_packages),
    )


def _externalizable_packages(environment: ResolvedEnvironment) -> tuple[ResolvedPackage, ...]:
    packages: dict[str, ResolvedPackage] = {package.name: package for package in environment.dependencies}
    dependency_graph = environment.dependency_graph
    runtime_index = environment.runtime_index
    if dependency_graph is None or runtime_index is None:
        return tuple(sorted(packages.values(), key=lambda item: item.name))

    for package_name, keys in dependency_graph.package_keys.items():
        if package_name in packages:
            continue
        runtime_package = runtime_index.packages.get(package_name)
        if runtime_package is None:
            continue
        if runtime_package.provider is not RuntimeProvider.CLOUDFLARE:
            continue

        candidate_versions = tuple(sorted({dependency_graph.nodes[key].version for key in keys}))
        runtime_version = runtime_package.versions[0] if len(runtime_package.versions) == 1 else None
        if not candidate_versions:
            runtime_status = RuntimeStatus.EXTERNAL_RUNTIME
        elif runtime_version is None:
            runtime_status = RuntimeStatus.EXTERNAL_RUNTIME
        elif runtime_version in candidate_versions:
            runtime_status = RuntimeStatus.EXTERNAL_RUNTIME
        elif environment.config.prefer_runtime_packages:
            runtime_status = RuntimeStatus.VERSION_CONFLICT
        else:
            runtime_status = RuntimeStatus.BUNDLED

        packages[package_name] = ResolvedPackage(
            name=package_name,
            version=candidate_versions[0] if len(candidate_versions) == 1 else "",
            source="uv.lock",
            runtime_status=runtime_status,
            candidate_versions=candidate_versions,
            resolved_keys=keys,
            runtime_provider=runtime_package.provider,
            runtime_version=runtime_version,
        )

    return tuple(sorted(packages.values(), key=lambda item: item.name))


def _should_externalize(environment: ResolvedEnvironment, package: ResolvedPackage) -> bool:
    if package.runtime_provider is not RuntimeProvider.CLOUDFLARE:
        return False
    if package.runtime_status == RuntimeStatus.EXTERNAL_RUNTIME:
        return True
    return package.runtime_status == RuntimeStatus.VERSION_CONFLICT and environment.config.prefer_runtime_packages


def _externalize_reason(package: ResolvedPackage) -> str:
    if package.runtime_status == RuntimeStatus.VERSION_CONFLICT:
        return "runtime_version_conflict_prefer_runtime"
    if package.runtime_version:
        return f"runtime_provided:{package.runtime_version}"
    return "runtime_provided"


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(Path(path).match(pattern) for pattern in patterns)


def _is_test_file(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_") or path.name.endswith("_test.py")


def _is_doc_file(path: Path) -> bool:
    return "docs" in path.parts or path.suffix.lower() in {".md", ".rst"}


def _is_example_file(path: Path) -> bool:
    return "examples" in path.parts


def _is_metadata_file(path: Path) -> bool:
    return any(part.endswith((".dist-info", ".egg-info")) for part in path.parts)
