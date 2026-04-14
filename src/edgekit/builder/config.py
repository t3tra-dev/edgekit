from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast

_DEFAULT_STRIP_METHODS = ("test_*",)


@dataclass(slots=True, frozen=True)
class BuilderSelection:
    modules: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class PackageProfile:
    name: str
    strip: tuple[str, ...] = ()
    side_effect_free_modules: tuple[str, ...] = ()
    keep_modules: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class BuilderConfig:
    entry: str | None = None
    compatibility_date: str | None = None
    mode: Literal["safe", "aggressive"] = "safe"
    prefer_runtime_packages: bool = True
    strip_tests: bool = True
    strip_docs: bool = True
    strip_examples: bool = False
    strip_metadata: bool = True
    strip_methods: tuple[str, ...] = _DEFAULT_STRIP_METHODS
    report: str = "build/edgekit/report.json"
    include: BuilderSelection = field(default_factory=BuilderSelection)
    exclude: BuilderSelection = field(default_factory=BuilderSelection)
    package_profiles: tuple[PackageProfile, ...] = ()

    def with_entry(self, entry: str | None) -> BuilderConfig:
        if entry is None:
            return self
        return replace(self, entry=entry)


def load_builder_config(pyproject_path: Path) -> BuilderConfig:
    if not pyproject_path.exists():
        return BuilderConfig()

    data = tomllib.loads(pyproject_path.read_text())
    builder = data.get("tool", {}).get("edgekit", {}).get("builder", {})

    include = builder.get("include", {})
    exclude = builder.get("exclude", {})
    package_profiles = _load_package_profiles(builder.get("package_profile"))

    return BuilderConfig(
        entry=builder.get("entry"),
        compatibility_date=builder.get("compatibility_date"),
        mode=builder.get("mode", "safe"),
        prefer_runtime_packages=bool(builder.get("prefer_runtime_packages", True)),
        strip_tests=bool(builder.get("strip_tests", True)),
        strip_docs=bool(builder.get("strip_docs", True)),
        strip_examples=bool(builder.get("strip_examples", False)),
        strip_metadata=bool(builder.get("strip_metadata", True)),
        strip_methods=_string_tuple(builder.get("strip_methods", _DEFAULT_STRIP_METHODS)),
        report=builder.get("report", "build/edgekit/report.json"),
        include=BuilderSelection(
            modules=tuple(include.get("modules", ())),
            files=tuple(include.get("files", ())),
        ),
        exclude=BuilderSelection(
            modules=tuple(exclude.get("modules", ())),
            files=tuple(exclude.get("files", ())),
        ),
        package_profiles=package_profiles,
    )


def _load_package_profiles(raw_profiles: object) -> tuple[PackageProfile, ...]:
    if not isinstance(raw_profiles, Sequence) or isinstance(raw_profiles, (str, bytes, bytearray)):
        return ()

    raw_profile_items = cast(Sequence[object], raw_profiles)
    profiles: list[PackageProfile] = []
    for raw_profile in raw_profile_items:
        if not isinstance(raw_profile, Mapping):
            continue
        profile_mapping = cast(Mapping[str, object], raw_profile)

        name = profile_mapping.get("name")
        if not isinstance(name, str) or not name:
            continue

        profiles.append(
            PackageProfile(
                name=name,
                strip=_string_tuple(profile_mapping.get("strip")),
                side_effect_free_modules=_string_tuple(profile_mapping.get("side_effect_free_modules")),
                keep_modules=_string_tuple(profile_mapping.get("keep_modules")),
            )
        )

    return tuple(profiles)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    items = [item for item in cast(Sequence[object], value) if isinstance(item, str)]
    return tuple(items)
