from __future__ import annotations

from collections.abc import Collection
from pathlib import Path


def normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def relative_path_from_root(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def module_name_from_relative_path(relative_path: Path) -> str | None:
    if relative_path.suffix != ".py":
        return None
    parts = list(relative_path.with_suffix("").parts)
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts.pop()
    if not parts:
        return None
    return ".".join(parts)


def module_with_package_ancestors(module_name: str) -> tuple[str, ...]:
    parts = [part for part in module_name.split(".") if part]
    if not parts:
        return ()
    ordered: list[str] = []
    for size in range(1, len(parts)):
        ordered.append(".".join(parts[:size]))
    ordered.append(module_name)
    return tuple(ordered)


def enqueue_module_with_ancestors(
    module_name: str,
    known_modules: Collection[str],
    reachable: set[str],
    pending: list[str],
) -> None:
    for candidate in module_with_package_ancestors(module_name):
        if candidate in known_modules and candidate not in reachable:
            reachable.add(candidate)
            pending.append(candidate)


def module_package_name(module_name: str, *, relative_path: Path | None = None) -> str:
    if relative_path is not None and relative_path.name == "__init__.py":
        return module_name
    return module_name.rpartition(".")[0]
