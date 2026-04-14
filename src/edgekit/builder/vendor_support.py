from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import Distribution
from pathlib import Path

from .common import normalize_package_name

_REQUIREMENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")


@dataclass(slots=True, frozen=True)
class VendorModuleSource:
    name: str
    distribution: str
    source_path: Path
    relative_path: Path
    source: str


def parse_requirement_name(requirement: str) -> str | None:
    match = _REQUIREMENT_NAME_RE.match(requirement)
    if match is None:
        return None
    return normalize_package_name(match.group(0))


def editable_source_roots(dist: Distribution) -> tuple[Path, ...]:
    dist_files = dist.files
    if dist_files is None:
        return ()

    roots: list[Path] = []
    for file in dist_files:
        relative_path = Path(str(file))
        if relative_path.suffix != ".pth":
            continue
        pth_path = Path(str(dist.locate_file(file))).resolve()
        for source_root in parse_pth_source_roots(pth_path):
            if source_root not in roots:
                roots.append(source_root)
    return tuple(roots)


def parse_pth_source_roots(pth_path: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    for line in pth_path.read_text().splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#") or candidate.startswith("import "):
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = (pth_path.parent / path).resolve()
        if path.exists() and path.is_dir() and path not in roots:
            roots.append(path)
    return tuple(roots)


def is_python_source_path(source_root: Path, relative_path: Path) -> bool:
    if relative_path.suffix != ".py":
        return False
    parts = relative_path.parts
    if not parts:
        return False
    if len(parts) == 1:
        return True
    package_root = source_root / parts[0]
    return (package_root / "__init__.py").exists()


def is_editable_runtime_path(source_root: Path, relative_path: Path) -> bool:
    if is_python_source_path(source_root, relative_path):
        return True
    parts = relative_path.parts
    if not parts:
        return False
    package_root = source_root / parts[0]
    return package_root.is_dir() and (package_root / "__init__.py").exists()


def replace_vendor_module_sources(
    module_sources_by_distribution: dict[str, tuple[VendorModuleSource, ...]],
    transformed_sources_by_module: dict[str, str],
) -> dict[str, tuple[VendorModuleSource, ...]]:
    return {
        distribution_name: tuple(
            VendorModuleSource(
                name=module_source.name,
                distribution=module_source.distribution,
                source_path=module_source.source_path,
                relative_path=module_source.relative_path,
                source=transformed_sources_by_module.get(module_source.name, module_source.source),
            )
            for module_source in module_sources
        )
        for distribution_name, module_sources in module_sources_by_distribution.items()
    }
