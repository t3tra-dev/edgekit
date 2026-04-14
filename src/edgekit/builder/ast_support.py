from __future__ import annotations

import ast


class VendorImportCollector(ast.NodeVisitor):
    def __init__(
        self,
        module_name: str,
        *,
        package_name: str,
        known_modules: frozenset[str],
    ) -> None:
        self._module_name = module_name
        self._package_name = package_name
        self._known_modules = known_modules
        self._type_checking_depth = 0
        self.imports: set[str] = set()
        self.dynamic_imports: set[str] = set()
        self.dynamic_keep_roots: set[str] = set()
        self.has_unknown_dynamic_import = False

    def visit_If(self, node: ast.If) -> None:
        if is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return

        static_truth = static_truth_value(node.test)
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
            self.imports.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._type_checking_depth:
            return
        module_name = resolve_relative_import(self._package_name, node.module, node.level)
        if not module_name:
            return
        if any(alias.name == "*" for alias in node.names):
            self.imports.add(module_name)
            return
        for alias in node.names:
            candidate_module = f"{module_name}.{alias.name}" if module_name else alias.name
            if candidate_module in self._known_modules:
                self.imports.add(candidate_module)
            else:
                self.imports.add(module_name)

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node.func)
        if name in {"__import__", "importlib.import_module"}:
            resolved_import = resolve_dynamic_import_call(node, current_package=self._package_name)
            if resolved_import is None:
                self.has_unknown_dynamic_import = True
                self.dynamic_keep_roots.update(
                    dynamic_keep_roots(node, module_name=self._module_name, package_name=self._package_name)
                )
            elif resolved_import:
                self.dynamic_imports.add(resolved_import)
        self.generic_visit(node)


def resolve_relative_import(current_package: str, imported: str | None, level: int) -> str:
    if level == 0:
        return imported or ""

    parts = [part for part in current_package.split(".") if part]
    if level > 1:
        if len(parts) < level - 1:
            return imported or ""
        parts = parts[: -(level - 1)]

    if not parts and imported is None:
        return imported or ""

    base = parts[:]
    if imported:
        base.append(imported)
    return ".".join(part for part in base if part)


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        root = call_name(node.value)
        if root is None:
            return None
        return f"{root}.{node.attr}"
    return None


def positional_argument(node: ast.Call, index: int) -> ast.AST | None:
    if len(node.args) <= index:
        return None
    return node.args[index]


def resolve_dynamic_import_call(node: ast.Call, *, current_package: str) -> str | None:
    name = call_name(node.func)
    raw_import = constant_string_argument(node, 0)
    if name not in {"__import__", "importlib.import_module"} or raw_import is None:
        return None
    if raw_import.startswith("."):
        package = dynamic_import_package(node, current_package=current_package)
        if package is None:
            return None
        resolved = resolve_relative_import(package, raw_import.lstrip(".") or None, relative_import_level(raw_import))
        return resolved or None
    return raw_import


def constant_string_argument(node: ast.Call, index: int) -> str | None:
    if len(node.args) <= index:
        return None
    value = node.args[index]
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def constant_string_arguments(node: ast.Call) -> tuple[str, ...]:
    values: list[str] = []
    for argument in node.args:
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            values.append(argument.value)
    return tuple(values)


def dynamic_import_package(node: ast.Call, *, current_package: str) -> str | None:
    if not node.args[0:1]:
        return current_package
    if len(node.args) > 1:
        return dynamic_import_package_value(node.args[1], current_package=current_package)
    for keyword in node.keywords:
        if keyword.arg == "package":
            return dynamic_import_package_value(keyword.value, current_package=current_package)
    return current_package


def dynamic_import_package_value(node: ast.AST, *, current_package: str) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id == "__package__":
        return current_package
    return None


def relative_import_level(raw_import: str) -> int:
    return len(raw_import) - len(raw_import.lstrip("."))


def dynamic_keep_roots(
    node: ast.Call,
    *,
    module_name: str,
    package_name: str,
) -> tuple[str, ...]:
    keep_roots: set[str] = set()
    current_root = current_dynamic_keep_root(module_name, package_name)

    if node.args and expression_references_current_package(
        node.args[0],
        module_name=module_name,
        package_name=package_name,
    ):
        keep_roots.add(current_root)

    package_argument = dynamic_import_package_argument(node)
    if package_argument is not None and expression_references_current_package(
        package_argument,
        module_name=module_name,
        package_name=package_name,
    ):
        keep_roots.add(current_root)

    return tuple(sorted(root for root in keep_roots if root))


def dynamic_import_package_argument(node: ast.Call) -> ast.AST | None:
    if len(node.args) > 1:
        return node.args[1]
    for keyword in node.keywords:
        if keyword.arg == "package":
            return keyword.value
    return None


def current_dynamic_keep_root(module_name: str, package_name: str) -> str:
    if package_name:
        return package_name
    if "." in module_name:
        return module_name.partition(".")[0]
    return module_name


def expression_references_current_package(
    node: ast.AST,
    *,
    module_name: str,
    package_name: str,
) -> bool:
    current_root = current_dynamic_keep_root(module_name, package_name)

    if isinstance(node, ast.Name):
        return node.id in {"__name__", "__package__"}
    if isinstance(node, ast.Attribute):
        return isinstance(node.value, ast.Name) and node.value.id == "__spec__" and node.attr == "parent"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return bool(
            current_root
            and (node.value == current_root or node.value.startswith(f"{current_root}.") or node.value.startswith("."))
        )
    if isinstance(node, ast.JoinedStr):
        return any(
            expression_references_current_package(value, module_name=module_name, package_name=package_name)
            for value in node.values
        )
    if isinstance(node, ast.FormattedValue):
        return expression_references_current_package(node.value, module_name=module_name, package_name=package_name)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return expression_references_current_package(
            node.left,
            module_name=module_name,
            package_name=package_name,
        ) or expression_references_current_package(
            node.right,
            module_name=module_name,
            package_name=package_name,
        )
    return False


def is_type_checking_guard(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id == "typing" and node.attr == "TYPE_CHECKING"
    return False


def static_truth_value(node: ast.AST) -> bool | None:
    if is_dunder_main_guard(node):
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def is_docstring_expr(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def is_class_member_reference(node: ast.AST, *, class_name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"self", "cls", class_name}
    return is_super_call(node)


def is_class_member_lookup_target(node: ast.AST | None, *, class_name: str) -> bool:
    return node is not None and is_class_member_reference(node, class_name=class_name)


def is_super_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super"


def is_dunder_main_guard(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "__name__"
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value == "__main__"
    )
