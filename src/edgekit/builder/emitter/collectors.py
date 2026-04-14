# pyright: reportPrivateUsage=false, reportUnusedFunction=false, reportUnusedClass=false
from __future__ import annotations

import ast

from ..ast_support import (
    call_name as _call_name,
)
from ..ast_support import (
    constant_string_argument as _constant_string_argument,
)
from ..ast_support import (
    constant_string_arguments as _constant_string_arguments,
)
from ..ast_support import (
    is_class_member_lookup_target as _is_class_member_lookup_target,
)
from ..ast_support import (
    is_class_member_reference as _is_class_member_reference,
)
from ..ast_support import (
    is_type_checking_guard as _is_type_checking_guard,
)
from ..ast_support import (
    positional_argument as _positional_argument,
)
from ..ast_support import (
    resolve_dynamic_import_call as _resolve_dynamic_import_call,
)
from ..ast_support import (
    resolve_relative_import as _resolve_relative_import,
)
from ..ast_support import (
    static_truth_value as _static_truth_value,
)
from .types import _ImportBinding, _RequestedExports


class _UsedAttributeCollector(ast.NodeVisitor):
    def __init__(self, *, package_name: str, known_modules: frozenset[str]) -> None:
        self._package_name = package_name
        self._known_modules = known_modules
        self._type_checking_depth = 0
        self._bindings: dict[str, _ImportBinding] = {}
        self.names: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return

        static_truth = _static_truth_value(node.test)
        if static_truth is True:
            for child in node.body:
                self.visit(child)
            return
        if static_truth is False:
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._type_checking_depth:
            return
        for alias in node.names:
            bound_name = alias.asname or alias.name.partition(".")[0]
            self._bindings[bound_name] = _ImportBinding(module_name=alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._type_checking_depth:
            return
        resolved_module = _resolve_relative_import(self._package_name, node.module, node.level)
        if not resolved_module:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname or alias.name
            self._bindings[bound_name] = _resolve_import_binding(
                resolved_module,
                alias.name,
                known_vendor_modules=self._known_modules,
            )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self._type_checking_depth == 0
            and isinstance(node.ctx, ast.Load)
            and not self._is_module_binding(node.value)
        ):
            self.names.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._type_checking_depth == 0:
            name = _call_name(node.func)
            if name in {"getattr", "hasattr", "setattr", "delattr"} and not self._is_module_binding(
                _positional_argument(node, 0)
            ):
                attribute_name = _constant_string_argument(node, 1)
                if attribute_name:
                    self.names.add(attribute_name)
            if name in {"attrgetter", "operator.attrgetter"}:
                for attribute_name in _constant_string_arguments(node):
                    for part in attribute_name.split("."):
                        if part:
                            self.names.add(part)
        self.generic_visit(node)

    def _is_module_binding(self, node: ast.AST | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Name):
            binding = self._bindings.get(node.id)
            return binding is not None and binding.imported_name is None
        if isinstance(node, ast.Attribute):
            return self._is_module_binding(node.value)
        return False


class _ClassMemberUsageCollector(ast.NodeVisitor):
    def __init__(self, *, class_name: str) -> None:
        self._class_name = class_name
        self._type_checking_depth = 0
        self.names: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return

        static_truth = _static_truth_value(node.test)
        if static_truth is True:
            for child in node.body:
                self.visit(child)
            return
        if static_truth is False:
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            self._type_checking_depth == 0
            and isinstance(node.ctx, ast.Load)
            and _is_class_member_reference(node.value, class_name=self._class_name)
        ):
            self.names.add(node.attr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._type_checking_depth == 0:
            name = _call_name(node.func)
            if name in {"getattr", "hasattr", "setattr", "delattr"} and _is_class_member_lookup_target(
                _positional_argument(node, 0),
                class_name=self._class_name,
            ):
                attribute_name = _constant_string_argument(node, 1)
                if attribute_name:
                    self.names.add(attribute_name)
        self.generic_visit(node)


class _RequestedExportCollector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        package_name: str,
        known_vendor_modules: frozenset[str],
        requested_exports: dict[str, _RequestedExports],
    ) -> None:
        self._package_name = package_name
        self._known_vendor_modules = known_vendor_modules
        self._requested_exports = requested_exports
        self._type_checking_depth = 0
        self._bindings: dict[str, _ImportBinding] = {}
        self._used_binding_names: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return

        static_truth = _static_truth_value(node.test)
        if static_truth is True:
            for child in node.body:
                self.visit(child)
            return
        if static_truth is False:
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._type_checking_depth:
            return
        for alias in node.names:
            bound_name = alias.asname or alias.name.partition(".")[0]
            self._bindings[bound_name] = _ImportBinding(module_name=alias.name)
            _mark_requested_export(self._requested_exports, alias.name, wildcard=True)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._type_checking_depth:
            return
        resolved_module = _resolve_relative_import(self._package_name, node.module, node.level)
        if not resolved_module:
            return
        for alias in node.names:
            if alias.name == "*":
                _mark_requested_export(self._requested_exports, resolved_module, wildcard=True)
                continue
            bound_name = alias.asname or alias.name
            binding = _resolve_import_binding(
                resolved_module,
                alias.name,
                known_vendor_modules=self._known_vendor_modules,
            )
            self._bindings[bound_name] = binding
            if binding.imported_name is None:
                _mark_requested_export(self._requested_exports, binding.module_name, wildcard=True)
            else:
                _mark_requested_export(self._requested_exports, binding.module_name, name=binding.imported_name)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._type_checking_depth == 0 and isinstance(node.ctx, ast.Load):
            binding = self._binding_for_name(node.value)
            if binding is not None and binding.imported_name is None:
                if isinstance(node.value, ast.Name):
                    self._used_binding_names.add(node.value.id)
                _mark_requested_export(self._requested_exports, binding.module_name, name=node.attr)
                return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self._type_checking_depth:
            return
        if not isinstance(node.ctx, ast.Load):
            return
        binding = self._bindings.get(node.id)
        if binding is None:
            return
        self._used_binding_names.add(node.id)
        if binding.imported_name is None:
            _mark_requested_export(self._requested_exports, binding.module_name, wildcard=True)
            return
        _mark_requested_export(self._requested_exports, binding.module_name, name=binding.imported_name)

    def visit_Call(self, node: ast.Call) -> None:
        if self._type_checking_depth == 0:
            name = _call_name(node.func)
            if name in {"__import__", "importlib.import_module"}:
                imported_module = _resolve_dynamic_import_call(node, current_package=self._package_name)
                if imported_module and imported_module in self._known_vendor_modules:
                    _mark_requested_export(self._requested_exports, imported_module, wildcard=True)
            if name in {"getattr", "hasattr", "setattr", "delattr"} and len(node.args) >= 2:
                binding = self._binding_for_name(node.args[0])
                attribute_name = _constant_string_argument(node, 1)
                if binding is not None and binding.imported_name is None and attribute_name:
                    if isinstance(node.args[0], ast.Name):
                        self._used_binding_names.add(node.args[0].id)
                    _mark_requested_export(self._requested_exports, binding.module_name, name=attribute_name)
                    for argument in node.args[2:]:
                        self.visit(argument)
                    for keyword in node.keywords:
                        self.visit(keyword.value)
                    return
        self.generic_visit(node)

    def _binding_for_name(self, node: ast.AST) -> _ImportBinding | None:
        if isinstance(node, ast.Name):
            return self._bindings.get(node.id)
        return None

    def finalize(self) -> None:
        for binding_name, binding in self._bindings.items():
            if binding.imported_name is None and binding_name not in self._used_binding_names:
                _mark_requested_export(self._requested_exports, binding.module_name, wildcard=True)


class _TopLevelStatementUsageCollector(ast.NodeVisitor):
    def __init__(self, import_bindings: dict[str, _ImportBinding]) -> None:
        self._import_bindings = import_bindings
        self._type_checking_depth = 0
        self.names: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return

        static_truth = _static_truth_value(node.test)
        if static_truth is True:
            for child in node.body:
                self.visit(child)
            return
        if static_truth is False:
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self._type_checking_depth:
            return
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)


def _mark_requested_export(
    requested_exports: dict[str, _RequestedExports],
    module_name: str,
    *,
    name: str | None = None,
    wildcard: bool = False,
) -> None:
    requested = requested_exports.setdefault(module_name, _RequestedExports())
    if wildcard:
        requested.wildcard = True
    if name:
        requested.names.add(name)


def _resolve_import_binding(
    resolved_module: str,
    imported_name: str,
    *,
    known_vendor_modules: frozenset[str],
) -> _ImportBinding:
    candidate_module = f"{resolved_module}.{imported_name}" if resolved_module else imported_name
    if candidate_module in known_vendor_modules:
        return _ImportBinding(module_name=candidate_module)
    return _ImportBinding(module_name=resolved_module, imported_name=imported_name)
