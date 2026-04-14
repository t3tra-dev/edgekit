from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from .doctor import doctor_project
from .mode import BuildMode
from .models import AnalysisResult, PrunedDistribution, PruneDecision, ResolvedEnvironment


class ReportSummary(TypedDict):
    kept_files: int
    dropped_files: int
    externalized_packages: int
    reachable_modules: int
    total_modules: int


class ReportPackage(TypedDict):
    status: str
    version: str
    candidate_versions: list[str]
    source: str
    runtime_version: str | None
    runtime_provider: str | None


class ReportModule(TypedDict):
    included: bool
    reason: str
    path: str
    is_package: bool
    dynamic_imports: list[str]
    type_checking_imports: list[str]
    has_unknown_dynamic_import: bool
    has_side_effect_risk: bool
    is_reexport_only: bool


class ReportManifest(TypedDict):
    entry: str
    compatibility_date: str | None
    compatibility_flags: list[str]
    mode: BuildMode
    lockfile: str | None
    runtime_index_source: str | None
    python_version: str | None
    pyodide_version: str | None
    packages: dict[str, ReportPackage]
    modules: dict[str, ReportModule]


class ReportRisk(TypedDict):
    level: str
    message: str
    path: str | None
    code: str


class ReportExplainPackage(TypedDict):
    status: str
    reason: str


class ReportExplainModule(TypedDict):
    path: str
    reason: str


class ReportExplain(TypedDict):
    packages: dict[str, ReportExplainPackage]
    modules: dict[str, ReportExplainModule]
    profiles: list[str]


class ReportDecision(TypedDict):
    target: str
    action: str
    reason: str
    kind: str


class ReportPayload(TypedDict):
    summary: ReportSummary
    manifest: ReportManifest
    explain: ReportExplain
    risks: list[ReportRisk]
    decisions: list[ReportDecision]


def report_payload(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    pruned: PrunedDistribution,
) -> ReportPayload:
    decision_index = {decision.target: decision for decision in pruned.decisions}
    doctor_report = doctor_project(environment, analysis, pruned)

    manifest: ReportManifest = {
        "entry": str(environment.entry.relative_to(environment.project_root)),
        "compatibility_date": environment.compatibility_date,
        "compatibility_flags": list(environment.compatibility_flags),
        "mode": environment.config.mode,
        "lockfile": (
            str(environment.lockfile_path.relative_to(environment.project_root))
            if environment.lockfile_path is not None
            else None
        ),
        "runtime_index_source": environment.runtime_index.source if environment.runtime_index is not None else None,
        "python_version": environment.runtime_index.python_version if environment.runtime_index is not None else None,
        "pyodide_version": environment.runtime_index.pyodide_version if environment.runtime_index is not None else None,
        "packages": {
            package.name: {
                "status": package.runtime_status.value,
                "version": package.version,
                "candidate_versions": list(package.candidate_versions),
                "source": package.source,
                "runtime_version": package.runtime_version,
                "runtime_provider": package.runtime_provider.value if package.runtime_provider is not None else None,
            }
            for package in environment.dependencies
        },
        "modules": {
            name: {
                "included": name in analysis.graph.reachable,
                "reason": decision_index.get(
                    str(node.path.relative_to(environment.project_root)), PruneDecision("", "drop", "unknown")
                ).reason,
                "path": str(node.path.relative_to(environment.project_root)),
                "is_package": node.is_package,
                "dynamic_imports": sorted(node.dynamic_imports),
                "type_checking_imports": sorted(node.type_checking_imports),
                "has_unknown_dynamic_import": node.has_unknown_dynamic_import,
                "has_side_effect_risk": node.has_side_effect_risk,
                "is_reexport_only": node.is_reexport_only,
            }
            for name, node in sorted(analysis.graph.nodes.items())
        },
    }

    return {
        "summary": {
            "kept_files": len(pruned.kept_files),
            "dropped_files": sum(1 for decision in pruned.decisions if decision.action == "drop"),
            "externalized_packages": len(pruned.externalized_packages),
            "reachable_modules": len(analysis.graph.reachable),
            "total_modules": len(analysis.graph.nodes),
        },
        "manifest": manifest,
        "explain": {
            "packages": {
                package.name: {
                    "status": package.runtime_status.value,
                    "reason": _package_reason(package),
                }
                for package in environment.dependencies
            },
            "modules": {
                name: {
                    "path": str(node.path.relative_to(environment.project_root)),
                    "reason": manifest["modules"][name]["reason"],
                }
                for name, node in sorted(analysis.graph.nodes.items())
            },
            "profiles": [profile.name for profile in environment.package_profiles],
        },
        "risks": [
            {"level": risk.level, "message": risk.message, "path": risk.path, "code": risk.code}
            for risk in doctor_report.items
        ],
        "decisions": [
            {"target": decision.target, "action": decision.action, "reason": decision.reason, "kind": decision.kind}
            for decision in pruned.decisions
        ],
    }


def render_report(payload: ReportPayload) -> str:
    summary = payload["summary"]
    risks = payload["risks"]
    externalized = sorted(
        decision["target"]
        for decision in payload["decisions"]
        if decision["action"] == "externalize" and decision["kind"] == "package"
    )

    lines = [
        "EdgeKit Build Report",
        f"kept_files: {summary['kept_files']}",
        f"dropped_files: {summary['dropped_files']}",
        f"externalized_packages: {summary['externalized_packages']}",
        f"reachable_modules: {summary['reachable_modules']} / {summary['total_modules']}",
    ]

    manifest = payload["manifest"]
    if manifest["lockfile"] is not None:
        lines.append(f"lockfile: {manifest['lockfile']}")
    if manifest["runtime_index_source"] is not None:
        lines.append(f"runtime_index: {manifest['runtime_index_source']}")
    if manifest["python_version"] is not None:
        lines.append(f"python_version: {manifest['python_version']}")
    if manifest["pyodide_version"] is not None:
        lines.append(f"pyodide_version: {manifest['pyodide_version']}")
    if externalized:
        lines.append(f"externalized: {', '.join(externalized)}")

    if risks:
        lines.append("")
        lines.append("Risks:")
        for risk in risks:
            location = f" ({risk['path']})" if risk.get("path") else ""
            lines.append(f"- {risk['level']}[{risk['code']}]: {risk['message']}{location}")

    return "\n".join(lines) + "\n"


def write_report(path: Path, payload: ReportPayload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def load_report(path: Path) -> ReportPayload:
    return cast(ReportPayload, json.loads(path.read_text()))


def _package_reason(package: object) -> str:
    from .models import ResolvedPackage, RuntimeStatus

    if not isinstance(package, ResolvedPackage):
        return "unknown"
    if package.runtime_status == RuntimeStatus.BUNDLED:
        if package.version:
            return f"bundled from {package.source} ({package.version})"
        return f"bundled from {package.source}"
    if package.runtime_status == RuntimeStatus.EXTERNAL_RUNTIME:
        if package.runtime_version:
            return f"provided by runtime ({package.runtime_version})"
        return "provided by runtime"
    if package.runtime_status == RuntimeStatus.VERSION_CONFLICT:
        return "runtime version conflicts with locked version"
    return "package is not known to be supported by runtime"
