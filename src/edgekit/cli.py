from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import (
    analyze_project,
    doctor_project,
    emit_distribution,
    load_report,
    prune_distribution,
    render_report,
    resolve_environment,
)
from .builder.artifacts import resolve_report_path
from .builder.report import report_payload


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return args.handler(args)
    except Exception as exc:
        print(f"edgekit: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edgekit")
    parser.add_argument("--project-root", default=".", help="Project root directory")

    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--entry")
    analyze_parser.add_argument("--mode", choices=("safe", "aggressive"))
    analyze_parser.add_argument("--json", action="store_true", dest="as_json")
    analyze_parser.set_defaults(handler=_handle_analyze)

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--entry")
    build_parser.add_argument("--mode", choices=("safe", "aggressive"))
    build_parser.add_argument("--output-dir")
    build_parser.set_defaults(handler=_handle_build)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--path")
    report_parser.add_argument("--json", action="store_true", dest="as_json")
    report_parser.set_defaults(handler=_handle_report)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--entry")
    doctor_parser.add_argument("--mode", choices=("safe", "aggressive"))
    doctor_parser.set_defaults(handler=_handle_doctor)

    return parser


def _handle_analyze(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    environment = resolve_environment(project_root, entry=args.entry, mode=args.mode)
    analysis = analyze_project(environment)
    pruned = prune_distribution(environment, analysis)
    payload = report_payload(environment, analysis, pruned)

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_report(payload), end="")
    return 0


def _handle_build(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    environment = resolve_environment(project_root, entry=args.entry, mode=args.mode)
    analysis = analyze_project(environment)
    pruned = prune_distribution(environment, analysis)
    doctor_report = doctor_project(environment, analysis, pruned)
    _print_risks(doctor_report)
    if doctor_report.has_errors:
        return 1
    output_dir = Path(args.output_dir) if args.output_dir else None
    build_dir = emit_distribution(environment, analysis, pruned, output_dir=output_dir)
    print(f"bundle: {build_dir}")
    print(
        f"report: {resolve_report_path(environment.project_root, environment.workspace_root, environment.config.report)}"
    )
    return 0


def _handle_report(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    if args.path:
        report_path = Path(args.path)
    else:
        environment = resolve_environment(project_root)
        report_path = resolve_report_path(
            environment.project_root, environment.workspace_root, environment.config.report
        )
    payload = load_report(report_path)

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_report(payload), end="")
    return 0


def _handle_doctor(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    environment = resolve_environment(project_root, entry=args.entry, mode=args.mode)
    analysis = analyze_project(environment)
    pruned = prune_distribution(environment, analysis)
    report = doctor_project(environment, analysis, pruned)

    if not report.items:
        print("No issues detected.")
        return 0

    _print_risks(report)

    return 1 if report.has_errors else 0


def _print_risks(report: object) -> None:
    from .builder.models import RiskReport

    if not isinstance(report, RiskReport):
        return
    for risk in report.items:
        location = f" ({risk.path})" if risk.path else ""
        print(f"{risk.level}[{risk.code}]: {risk.message}{location}")
