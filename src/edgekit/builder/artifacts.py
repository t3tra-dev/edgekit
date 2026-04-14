from __future__ import annotations

from pathlib import Path


def resolve_workspace_root(project_root: Path) -> Path:
    project_root = project_root.resolve()
    for candidate in (project_root, *project_root.parents):
        if (candidate / ".git").exists():
            return candidate
    return project_root


def project_relative_root(project_root: Path, workspace_root: Path) -> Path | None:
    try:
        relative = project_root.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return None
    return None if relative == Path() or relative == Path(".") else relative


def resolve_build_root(project_root: Path, workspace_root: Path, *, output_dir: Path | None = None) -> Path:
    if output_dir is not None:
        return output_dir.resolve()

    relative_root = project_relative_root(project_root, workspace_root)
    if relative_root is None:
        return workspace_root / "build" / "edgekit" / "wrangler"
    return workspace_root / "build" / "projects" / relative_root / "edgekit" / "wrangler"


def resolve_report_path(project_root: Path, workspace_root: Path, report: str) -> Path:
    relative_root = project_relative_root(project_root, workspace_root)
    if relative_root is None:
        return workspace_root / report
    return workspace_root / "build" / "projects" / relative_root / "edgekit" / "report.json"
