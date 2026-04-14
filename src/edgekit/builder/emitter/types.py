# pyright: reportPrivateUsage=false, reportUnusedClass=false
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from ..vendor_support import VendorModuleSource

_STRIPPED_METHOD_MESSAGE = "This method was stripped from the Workers bundle by EdgeKit."
_CLASS_MEMBER_METHOD_PATTERNS = ("test_*",)

_VendorModuleSource = VendorModuleSource


def _str_set_default() -> set[str]:
    return set()


@dataclass(slots=True, frozen=True)
class _DistributionSourceFile:
    distribution: str
    source_path: Path
    relative_path: Path


@dataclass(slots=True)
class _VendorModuleNode:
    name: str
    distribution: str
    source_path: Path
    relative_path: Path
    imports: set[str] = field(default_factory=_str_set_default)
    dynamic_imports: set[str] = field(default_factory=_str_set_default)
    dynamic_keep_roots: set[str] = field(default_factory=_str_set_default)
    has_unknown_dynamic_import: bool = False


@dataclass(slots=True)
class _RequestedExports:
    names: set[str] = field(default_factory=_str_set_default)
    wildcard: bool = False


@dataclass(slots=True, frozen=True)
class _ImportBinding:
    module_name: str
    imported_name: str | None = None


@dataclass(slots=True)
class _ModuleStatementInfo:
    node: ast.stmt
    start_line: int
    end_line: int
    provided_names: set[str] = field(default_factory=_str_set_default)
    used_names: set[str] = field(default_factory=_str_set_default)
    droppable: bool = False


@dataclass(slots=True)
class _ClassMemberInfo:
    node: ast.stmt
    start_line: int
    end_line: int
    provided_names: set[str] = field(default_factory=_str_set_default)
    used_names: set[str] = field(default_factory=_str_set_default)
    droppable: bool = False


@dataclass(slots=True)
class _VendorPruningIndex:
    reachable_modules: set[str]
    reachable_roots_by_distribution: dict[str, set[str]]
    module_paths_by_distribution: dict[str, dict[str, str]]
    protected_method_names: frozenset[str]
    transformed_sources_by_module: dict[str, str]
