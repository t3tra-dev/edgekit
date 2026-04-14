from __future__ import annotations

from .barriers import collect_symbol_pruning_barrier_risks
from .binding_validation import collect_binding_validation_risks
from .models import AnalysisResult, PrunedDistribution, ResolvedEnvironment, RiskReport, RuntimeStatus


def doctor_project(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    pruned: PrunedDistribution | None = None,
) -> RiskReport:
    report = RiskReport()
    kept_paths = (
        {str(path.relative_to(environment.project_root)) for path in pruned.kept_files} if pruned is not None else None
    )
    externalized_distributions = (
        frozenset(package.name for package in pruned.externalized_packages) if pruned is not None else frozenset[str]()
    )
    reachable_paths = {
        str(analysis.graph.nodes[module_name].path.relative_to(environment.project_root))
        for module_name in analysis.graph.reachable
        if module_name in analysis.graph.nodes
    }

    for package in environment.dependencies:
        if package.runtime_status == RuntimeStatus.UNSUPPORTED:
            report.add(
                "error",
                _unsupported_package_message(package.name, package.version),
                path=package.name,
                code="unsupported_package",
            )
            continue
        if package.runtime_status == RuntimeStatus.VERSION_CONFLICT:
            report.add(
                "warning",
                _version_conflict_message(
                    package.name,
                    package.version,
                    package.candidate_versions,
                    package.runtime_version,
                ),
                path=package.name,
                code="version_conflict",
            )

    for risk in analysis.risks.items:
        if risk.path is not None and risk.path not in reachable_paths:
            continue
        if kept_paths is not None and risk.path is not None and risk.path not in kept_paths:
            continue
        if risk.code == "top_level_side_effect":
            report.add(
                "warning",
                "Module has top-level side effects and cannot be safely shaken",
                path=risk.path,
                code=risk.code,
            )
            continue
        report.add(risk.level, risk.message, path=risk.path, code=risk.code)

    for risk in collect_symbol_pruning_barrier_risks(
        environment,
        analysis,
        externalized_distributions=externalized_distributions,
    ).items:
        report.add(risk.level, risk.message, path=risk.path, code=risk.code)

    for risk in collect_binding_validation_risks(environment).items:
        report.add(risk.level, risk.message, path=risk.path, code=risk.code)

    return report


def _unsupported_package_message(name: str, version: str) -> str:
    if version:
        return f"Unsupported package for Workers runtime: {name} ({version})"
    return f"Unsupported package for Workers runtime: {name}"


def _version_conflict_message(
    name: str,
    version: str,
    candidate_versions: tuple[str, ...],
    runtime_version: str | None,
) -> str:
    locked = version or ", ".join(candidate_versions) or "unresolved"
    if runtime_version is None:
        return f"Runtime version conflicts with dependency resolution: {name} (lock={locked}, runtime=unknown)"
    return f"Runtime version conflicts with dependency resolution: {name} (lock={locked}, runtime={runtime_version})"
