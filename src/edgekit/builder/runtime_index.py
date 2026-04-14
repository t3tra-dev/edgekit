from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .artifacts import resolve_workspace_root
from .common import normalize_package_name as _normalize_package_name
from .models import RuntimeAvailabilityIndex, RuntimePackageAvailability, RuntimeProvider

_DOCS_USER_AGENT = "edgekit-builder/0.1"
_PYODIDE_PACKAGE_LIST_URL = "https://pyodide.org/en/{version}/usage/packages-in-pyodide.html"


@dataclass(slots=True, frozen=True)
class PythonRuntimeSpec:
    version: Literal["3.12", "3.13"]
    compat_flag: str
    compat_date: str | None
    pyodide_version: str


_PYTHON_RUNTIME_SPECS: tuple[PythonRuntimeSpec, ...] = (
    PythonRuntimeSpec(
        version="3.13",
        compat_flag="python_workers_20250116",
        compat_date="2025-09-29",
        pyodide_version="0.28.3",
    ),
    PythonRuntimeSpec(
        version="3.12",
        compat_flag="python_workers",
        compat_date=None,
        pyodide_version="0.27.7",
    ),
)


def resolve_runtime_index(
    project_root: Path,
    *,
    compatibility_date: str | None,
    compatibility_flags: tuple[str, ...],
) -> RuntimeAvailabilityIndex:
    python_spec = _resolve_python_runtime_spec(compatibility_date, compatibility_flags)
    if python_spec is None:
        return RuntimeAvailabilityIndex(
            compatibility_date=compatibility_date,
            source="unresolved",
        )

    pyodide_packages, pyodide_source = _load_pyodide_packages(project_root, python_spec.pyodide_version)
    packages = _core_runtime_packages(pyodide_packages, python_spec.pyodide_version)

    return RuntimeAvailabilityIndex(
        compatibility_date=compatibility_date,
        python_version=python_spec.version,
        pyodide_version=python_spec.pyodide_version,
        source=pyodide_source,
        packages=packages,
    )


def _resolve_python_runtime_spec(
    compatibility_date: str | None,
    compatibility_flags: tuple[str, ...],
) -> PythonRuntimeSpec | None:
    if "python_workers" not in compatibility_flags:
        return None

    compat_value = _compatibility_date_value(compatibility_date)
    for spec in _PYTHON_RUNTIME_SPECS:
        if spec.compat_flag in compatibility_flags:
            return spec
        if spec.compat_date is not None and compat_value is not None and compat_value >= spec.compat_date:
            return spec
    return None


def _compatibility_date_value(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def _load_pyodide_packages(
    project_root: Path, pyodide_version: str
) -> tuple[dict[str, RuntimePackageAvailability], str]:
    cache_path = _runtime_cache_path(project_root, pyodide_version)
    cached = _load_cached_runtime_packages(cache_path)
    if cached is not None:
        return cached, f"pyodide-cache:{pyodide_version}"

    source_url = _PYODIDE_PACKAGE_LIST_URL.format(version=pyodide_version)
    try:
        html = _download_text(source_url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return {}, f"builtin-core:{pyodide_version}"

    packages = _parse_pyodide_package_list(html)
    _write_cached_runtime_packages(cache_path, packages)
    return packages, f"pyodide-docs:{pyodide_version}"


def _runtime_cache_path(project_root: Path, pyodide_version: str) -> Path:
    workspace_root = resolve_workspace_root(project_root)
    return workspace_root / ".cache" / "edgekit" / "runtime" / f"pyodide-{pyodide_version}.json"


def _load_cached_runtime_packages(cache_path: Path) -> dict[str, RuntimePackageAvailability] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    payload_mapping = cast(Mapping[str, object], payload)

    raw_packages_object = payload_mapping.get("packages")
    if not isinstance(raw_packages_object, Mapping):
        return None
    raw_packages = cast(Mapping[object, object], raw_packages_object)

    packages: dict[str, RuntimePackageAvailability] = {}
    for raw_name, raw_info_object in raw_packages.items():
        if not isinstance(raw_name, str) or not isinstance(raw_info_object, Mapping):
            continue
        raw_info = cast(Mapping[str, object], raw_info_object)
        version = raw_info.get("version")
        if not isinstance(version, str):
            continue
        packages[raw_name] = RuntimePackageAvailability(
            name=raw_name,
            versions=(version,),
            provider=RuntimeProvider.PYODIDE,
        )
    return packages


def _write_cached_runtime_packages(cache_path: Path, packages: dict[str, RuntimePackageAvailability]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "packages": {
            name: {"version": availability.versions[0] if availability.versions else ""}
            for name, availability in sorted(packages.items())
            if availability.versions
        }
    }
    cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _download_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": _DOCS_USER_AGENT})
    with urlopen(request, timeout=10) as response:
        data = response.read()
    return data.decode("utf-8")


def _parse_pyodide_package_list(html: str) -> dict[str, RuntimePackageAvailability]:
    parser = _PyodidePackageListParser()
    parser.feed(html)

    packages: dict[str, RuntimePackageAvailability] = {}
    cells = parser.cells
    for index in range(0, len(cells) - 1, 2):
        name = _normalize_package_name(cells[index])
        version = cells[index + 1].strip()
        if not name or not version or name == "name" or version == "version":
            continue
        packages[name] = RuntimePackageAvailability(
            name=name,
            versions=(version,),
            provider=RuntimeProvider.PYODIDE,
        )
    return packages


def _core_runtime_packages(
    pyodide_packages: dict[str, RuntimePackageAvailability],
    pyodide_version: str,
) -> dict[str, RuntimePackageAvailability]:
    packages = dict(pyodide_packages)
    packages["pyodide-py"] = RuntimePackageAvailability(
        name="pyodide-py",
        versions=(pyodide_version,),
        provider=RuntimeProvider.CLOUDFLARE,
        notes="Provided by the Python Workers runtime",
    )

    for package_name in ("workers-py", "workers-runtime-sdk"):
        installed_version = _installed_distribution_version(package_name)
        versions = (installed_version,) if installed_version is not None else ()
        packages[package_name] = RuntimePackageAvailability(
            name=package_name,
            versions=versions,
            provider=RuntimeProvider.CLOUDFLARE,
            notes="Provided by the Python Workers runtime/toolchain",
        )

    return packages


def _installed_distribution_version(distribution_name: str) -> str | None:
    try:
        return distribution(distribution_name).version
    except PackageNotFoundError:
        return None


class _PyodidePackageListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_td = False
        self.current = ""
        self.cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "td":
            self.in_td = True
            self.current = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self.in_td:
            self.cells.append(self.current.strip())
            self.current = ""
            self.in_td = False

    def handle_data(self, data: str) -> None:
        if self.in_td:
            self.current += data
