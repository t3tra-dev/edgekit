from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from .config import BuilderConfig, PackageProfile


class RuntimeStatus(StrEnum):
    BUNDLED = "bundled"
    EXTERNAL_RUNTIME = "external_runtime"
    UNSUPPORTED = "unsupported"
    VERSION_CONFLICT = "version_conflict"


class RuntimeProvider(StrEnum):
    CLOUDFLARE = "cloudflare"
    PYODIDE = "pyodide"
    STDLIB = "stdlib"
    UNKNOWN = "unknown"


def _runtime_packages_default() -> dict[str, RuntimePackageAvailability]:
    return {}


def _dependency_nodes_default() -> dict[str, DependencyNode]:
    return {}


def _dependency_package_keys_default() -> dict[str, tuple[str, ...]]:
    return {}


@dataclass(slots=True, frozen=True)
class RuntimePackageAvailability:
    name: str
    versions: tuple[str, ...] = ()
    provider: RuntimeProvider = RuntimeProvider.UNKNOWN
    notes: str | None = None


@dataclass(slots=True, frozen=True)
class RuntimeAvailabilityIndex:
    compatibility_date: str | None
    pyodide_version: str | None = None
    python_version: str | None = None
    source: str = "unresolved"
    packages: dict[str, RuntimePackageAvailability] = field(default_factory=_runtime_packages_default)


@dataclass(slots=True, frozen=True)
class DependencyNode:
    key: str
    name: str
    version: str
    marker: str | None = None
    dependencies: tuple[str, ...] = ()
    source: str = "uv.lock"
    direct: bool = False


@dataclass(slots=True, frozen=True)
class ResolvedDependencyGraph:
    nodes: dict[str, DependencyNode] = field(default_factory=_dependency_nodes_default)
    package_keys: dict[str, tuple[str, ...]] = field(default_factory=_dependency_package_keys_default)
    roots: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ResolvedPackage:
    name: str
    version: str = ""
    source: str = "pyproject"
    runtime_status: RuntimeStatus = RuntimeStatus.BUNDLED
    candidate_versions: tuple[str, ...] = ()
    resolved_keys: tuple[str, ...] = ()
    runtime_provider: RuntimeProvider | None = None
    runtime_version: str | None = None


@dataclass(slots=True, frozen=True)
class ResolvedEnvironment:
    workspace_root: Path
    project_root: Path
    pyproject_path: Path
    wrangler_path: Path | None
    wrangler_config: Mapping[str, object]
    lockfile_path: Path | None
    entry: Path
    compatibility_date: str | None
    compatibility_flags: tuple[str, ...]
    config: BuilderConfig
    dependencies: tuple[ResolvedPackage, ...]
    dependency_graph: ResolvedDependencyGraph | None = None
    runtime_index: RuntimeAvailabilityIndex | None = None
    package_profiles: tuple[PackageProfile, ...] = ()


@dataclass(slots=True)
class ModuleNode:
    name: str
    path: Path
    imports: set[str] = field(default_factory=set[str])
    dynamic_imports: set[str] = field(default_factory=set[str])
    type_checking_imports: set[str] = field(default_factory=set[str])
    dynamic_keep_roots: set[str] = field(default_factory=set[str])
    has_unknown_dynamic_import: bool = False
    has_side_effect_risk: bool = False
    is_package: bool = False
    is_reexport_only: bool = False


@dataclass(slots=True)
class ModuleGraph:
    entry_module: str
    nodes: dict[str, ModuleNode]
    reachable: set[str]
    reasons: dict[str, str]


@dataclass(slots=True, frozen=True)
class Risk:
    level: Literal["info", "warning", "error"]
    message: str
    path: str | None = None
    code: str = "generic"


@dataclass(slots=True)
class RiskReport:
    items: list[Risk] = field(default_factory=list[Risk])

    def add(
        self,
        level: Literal["info", "warning", "error"],
        message: str,
        *,
        path: str | None = None,
        code: str = "generic",
    ) -> None:
        self.items.append(Risk(level=level, message=message, path=path, code=code))

    @property
    def has_errors(self) -> bool:
        return any(item.level == "error" for item in self.items)


@dataclass(slots=True)
class AnalysisResult:
    graph: ModuleGraph
    risks: RiskReport


@dataclass(slots=True, frozen=True)
class PruneDecision:
    target: str
    action: Literal["keep", "drop", "externalize"]
    reason: str
    kind: Literal["module", "file", "package"] = "file"


@dataclass(slots=True)
class PrunedDistribution:
    kept_files: list[Path]
    decisions: list[PruneDecision]
    externalized_packages: tuple[ResolvedPackage, ...] = ()
