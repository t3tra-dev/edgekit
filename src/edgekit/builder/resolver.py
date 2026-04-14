from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Literal, cast

from .artifacts import resolve_workspace_root
from .common import normalize_package_name as _normalize_package_name
from .config import PackageProfile, load_builder_config
from .models import (
    DependencyNode,
    ResolvedDependencyGraph,
    ResolvedEnvironment,
    ResolvedPackage,
    RuntimeAvailabilityIndex,
    RuntimeStatus,
)
from .profiles import effective_package_profiles
from .runtime_index import resolve_runtime_index

_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")


def resolve_environment(
    project_root: Path,
    *,
    entry: str | None = None,
    mode: Literal["safe", "aggressive"] | None = None,
) -> ResolvedEnvironment:
    project_root = project_root.resolve()
    workspace_root = resolve_workspace_root(project_root)
    pyproject_path = project_root / "pyproject.toml"
    wrangler_path = _resolve_wrangler_path(project_root)
    lockfile_path = _resolve_lockfile_path(project_root)

    config = load_builder_config(pyproject_path).with_entry(entry)
    if mode is not None:
        config = replace(config, mode=mode)
    pyproject = _load_toml_mapping(pyproject_path) if pyproject_path.exists() else {}
    wrangler = _load_jsonc(wrangler_path) if wrangler_path is not None else {}
    lockfile = _load_toml_mapping(lockfile_path) if lockfile_path is not None else {}
    compatibility_flags = _mapping_str_tuple(wrangler, "compatibility_flags")

    entry_value = config.entry or _mapping_str(wrangler, "main")
    if not entry_value:
        raise ValueError("Builder entrypoint is not configured")

    entry_path = (project_root / entry_value).resolve()
    if not entry_path.exists():
        raise ValueError(f"Entrypoint does not exist: {entry_value}")
    compatibility_date = config.compatibility_date or _mapping_str(wrangler, "compatibility_date")
    dependency_graph = _parse_lockfile_graph(lockfile, pyproject)
    runtime_index = resolve_runtime_index(
        project_root,
        compatibility_date=compatibility_date,
        compatibility_flags=compatibility_flags,
    )
    dependencies = tuple(
        _classify_resolved_package(package, runtime_index, prefer_runtime_packages=config.prefer_runtime_packages)
        for package in _parse_dependencies(pyproject, dependency_graph)
    )
    package_profiles = _active_package_profiles(
        effective_package_profiles(config.package_profiles),
        dependencies=dependencies,
        dependency_graph=dependency_graph,
    )

    return ResolvedEnvironment(
        workspace_root=workspace_root,
        project_root=project_root,
        pyproject_path=pyproject_path,
        wrangler_path=wrangler_path,
        wrangler_config=wrangler,
        lockfile_path=lockfile_path,
        entry=entry_path,
        compatibility_date=compatibility_date,
        compatibility_flags=compatibility_flags,
        config=config,
        dependencies=dependencies,
        dependency_graph=dependency_graph,
        runtime_index=runtime_index,
        package_profiles=package_profiles,
    )


def _parse_dependencies(
    pyproject: dict[str, object],
    dependency_graph: ResolvedDependencyGraph | None,
) -> list[ResolvedPackage]:
    project = pyproject.get("project")
    if not isinstance(project, Mapping):
        return []

    project_mapping = cast(Mapping[str, object], project)
    dependencies = project_mapping.get("dependencies")
    if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes, bytearray)):
        return []

    dependency_items = cast(Sequence[object], dependencies)
    packages: list[ResolvedPackage] = []

    for dependency in dependency_items:
        if not isinstance(dependency, str):
            continue
        match = _PACKAGE_NAME_RE.match(dependency)
        if match is None:
            continue
        package_name = _normalize_package_name(match.group(0))
        resolved_keys = dependency_graph.package_keys.get(package_name, ()) if dependency_graph is not None else ()
        candidate_versions = (
            tuple(sorted({dependency_graph.nodes[key].version for key in resolved_keys}))
            if dependency_graph is not None
            else ()
        )
        version = candidate_versions[0] if len(candidate_versions) == 1 else ""
        source = "uv.lock" if version else "pyproject"
        packages.append(
            ResolvedPackage(
                name=package_name,
                version=version,
                source=source,
                candidate_versions=candidate_versions,
                resolved_keys=resolved_keys,
            )
        )

    return packages


def _resolve_wrangler_path(project_root: Path) -> Path | None:
    for candidate in ("wrangler.jsonc", "wrangler.toml", "wrangler.json"):
        path = project_root / candidate
        if path.exists():
            return path
    return None


def _resolve_lockfile_path(project_root: Path) -> Path | None:
    path = project_root / "uv.lock"
    if path.exists():
        return path
    return None


def _load_jsonc(path: Path) -> dict[str, object]:
    if path.suffix == ".toml":
        return _load_toml_mapping(path)
    return cast(dict[str, object], json.loads(_strip_jsonc_comments(path.read_text())))


def _load_toml_mapping(path: Path) -> dict[str, object]:
    return cast(dict[str, object], tomllib.loads(path.read_text()))


def _mapping_str(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if isinstance(value, str):
        return value
    return None


def _mapping_str_tuple(mapping: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in cast(Sequence[object], value) if isinstance(item, str))


def _parse_lockfile_graph(
    lockfile: Mapping[str, object],
    pyproject: Mapping[str, object],
) -> ResolvedDependencyGraph | None:
    raw_packages = lockfile.get("package")
    if not isinstance(raw_packages, Sequence) or isinstance(raw_packages, (str, bytes, bytearray)):
        return None

    direct_names = set(_direct_dependency_names(pyproject))
    nodes: dict[str, DependencyNode] = {}
    package_keys: dict[str, list[str]] = {}
    raw_package_items = cast(Sequence[object], raw_packages)

    for raw_package in raw_package_items:
        if not isinstance(raw_package, Mapping):
            continue
        package_mapping = cast(Mapping[str, object], raw_package)

        name = _mapping_str(package_mapping, "name")
        version = _mapping_str(package_mapping, "version")
        if not name or not version:
            continue

        normalized_name = _normalize_package_name(name)
        marker = _dependency_marker(package_mapping.get("resolution-markers"))
        dependencies = _dependency_names(package_mapping.get("dependencies"))
        key = _dependency_key(normalized_name, version, marker)

        nodes[key] = DependencyNode(
            key=key,
            name=normalized_name,
            version=version,
            marker=marker,
            dependencies=dependencies,
            direct=normalized_name in direct_names,
        )
        package_keys.setdefault(normalized_name, []).append(key)

    roots = tuple(key for package_name in sorted(direct_names) for key in sorted(package_keys.get(package_name, ())))
    return ResolvedDependencyGraph(
        nodes=dict(sorted(nodes.items())),
        package_keys={name: tuple(sorted(keys)) for name, keys in sorted(package_keys.items())},
        roots=roots,
    )


def _direct_dependency_names(pyproject: Mapping[str, object]) -> tuple[str, ...]:
    project = pyproject.get("project")
    if not isinstance(project, Mapping):
        return ()

    project_mapping = cast(Mapping[str, object], project)
    raw_dependencies = project_mapping.get("dependencies")
    if not isinstance(raw_dependencies, Sequence) or isinstance(raw_dependencies, (str, bytes, bytearray)):
        return ()

    raw_dependency_items = cast(Sequence[object], raw_dependencies)
    names: list[str] = []
    for raw_dependency in raw_dependency_items:
        if not isinstance(raw_dependency, str):
            continue
        match = _PACKAGE_NAME_RE.match(raw_dependency)
        if match is None:
            continue
        names.append(_normalize_package_name(match.group(0)))
    return tuple(names)


def _dependency_names(raw_dependencies: object) -> tuple[str, ...]:
    if not isinstance(raw_dependencies, Sequence) or isinstance(raw_dependencies, (str, bytes, bytearray)):
        return ()

    raw_dependency_items = cast(Sequence[object], raw_dependencies)
    names: list[str] = []
    for raw_dependency in raw_dependency_items:
        if not isinstance(raw_dependency, Mapping):
            continue
        dependency_mapping = cast(Mapping[str, object], raw_dependency)
        name = _mapping_str(dependency_mapping, "name")
        if isinstance(name, str):
            names.append(_normalize_package_name(name))
    return tuple(names)


def _dependency_marker(raw_marker: object) -> str | None:
    if isinstance(raw_marker, str):
        return raw_marker
    if isinstance(raw_marker, Sequence) and not isinstance(raw_marker, (str, bytes, bytearray)):
        markers = [marker for marker in cast(Sequence[object], raw_marker) if isinstance(marker, str)]
        if not markers:
            return None
        return " or ".join(markers)
    return None


def _dependency_key(name: str, version: str, marker: str | None) -> str:
    if marker is None:
        return f"{name}@{version}"
    return f"{name}@{version} [{marker}]"


def _active_package_profiles(
    package_profiles: tuple[PackageProfile, ...],
    *,
    dependencies: tuple[ResolvedPackage, ...],
    dependency_graph: ResolvedDependencyGraph | None,
) -> tuple[PackageProfile, ...]:
    active_names = {_normalize_package_name(package.name) for package in dependencies}
    if dependency_graph is not None:
        active_names.update(dependency_graph.package_keys)
    return tuple(profile for profile in package_profiles if _normalize_package_name(profile.name) in active_names)


def _classify_resolved_package(
    package: ResolvedPackage,
    runtime_index: RuntimeAvailabilityIndex,
    *,
    prefer_runtime_packages: bool,
) -> ResolvedPackage:
    runtime_package = runtime_index.packages.get(package.name)
    if runtime_package is None:
        if _is_package_unsupported(package.name):
            return replace(package, runtime_status=RuntimeStatus.UNSUPPORTED)
        return package

    runtime_version = runtime_package.versions[0] if len(runtime_package.versions) == 1 else None
    if not package.candidate_versions:
        status = RuntimeStatus.EXTERNAL_RUNTIME
    elif runtime_version is None:
        status = RuntimeStatus.EXTERNAL_RUNTIME
    elif runtime_version in package.candidate_versions:
        status = RuntimeStatus.EXTERNAL_RUNTIME
    elif prefer_runtime_packages:
        status = RuntimeStatus.VERSION_CONFLICT
    else:
        status = RuntimeStatus.BUNDLED

    return replace(
        package,
        runtime_status=status,
        runtime_provider=runtime_package.provider,
        runtime_version=runtime_version,
    )


def _is_package_unsupported(package_name: str) -> bool:
    try:
        dist = distribution(package_name)
    except PackageNotFoundError:
        return False

    for file in dist.files or ():
        suffix = Path(str(file)).suffix.lower()
        if suffix in {".so", ".pyd", ".dylib", ".dll"}:
            return True
    return False


def _strip_jsonc_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(text):
        char = text[index]

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < len(text):
            next_char = text[index + 1]
            if next_char == "/":
                index += 2
                while index < len(text) and text[index] not in "\r\n":
                    index += 1
                continue
            if next_char == "*":
                index += 2
                while index + 1 < len(text) and text[index : index + 2] != "*/":
                    index += 1
                index += 2
                continue

        result.append(char)
        index += 1

    return "".join(result)
