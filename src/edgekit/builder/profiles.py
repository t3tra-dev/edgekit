from __future__ import annotations

from .common import normalize_package_name as _normalize_package_name
from .config import PackageProfile

_BUILTIN_PACKAGE_PROFILES: tuple[PackageProfile, ...] = (
    PackageProfile(
        name="pydantic",
        strip=("tests", "docs", "benchmarks"),
        side_effect_free_modules=("pydantic.alias_generators",),
        keep_modules=("pydantic.plugin_loader",),
    ),
    PackageProfile(
        name="webtypy",
        strip=("tests", "docs", "examples"),
    ),
)


def effective_package_profiles(user_profiles: tuple[PackageProfile, ...]) -> tuple[PackageProfile, ...]:
    merged: dict[str, PackageProfile] = {}
    for profile in _BUILTIN_PACKAGE_PROFILES + user_profiles:
        key = _normalize_package_name(profile.name)
        existing = merged.get(key)
        if existing is None:
            merged[key] = profile
            continue
        merged[key] = PackageProfile(
            name=profile.name,
            strip=_merge_string_tuple(existing.strip, profile.strip),
            side_effect_free_modules=_merge_string_tuple(
                existing.side_effect_free_modules,
                profile.side_effect_free_modules,
            ),
            keep_modules=_merge_string_tuple(existing.keep_modules, profile.keep_modules),
        )
    return tuple(sorted(merged.values(), key=lambda profile: profile.name))


def _merge_string_tuple(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({*left, *right}))
