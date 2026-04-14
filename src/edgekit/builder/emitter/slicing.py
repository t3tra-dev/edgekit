# pyright: reportPrivateUsage=false, reportUnusedFunction=false
from __future__ import annotations

import ast

from ..barriers import symbol_pruning_barrier_scopes
from ..mode import BuildMode
from ..models import AnalysisResult, ResolvedEnvironment
from .collectors import (
    _call_name,
    _ClassMemberUsageCollector,
    _constant_string_argument,
    _is_class_member_lookup_target,
    _positional_argument,
    _RequestedExportCollector,
    _resolve_import_binding,
    _resolve_relative_import,
    _TopLevelStatementUsageCollector,
    _UsedAttributeCollector,
)
from .graph import (
    _collect_vendor_nodes,
    _compute_reachable_vendor_modules,
    _replace_vendor_module_sources,
)
from .transform import (
    _has_strippable_method_body,
    _is_protocol_class,
    _matches_method_pattern,
    _statement_indent,
)
from .types import (
    _CLASS_MEMBER_METHOD_PATTERNS,
    _ClassMemberInfo,
    _ImportBinding,
    _ModuleStatementInfo,
    _RequestedExports,
    _VendorModuleSource,
)


def _build_symbol_sliced_vendor_sources(
    environment: ResolvedEnvironment,
    analysis: AnalysisResult,
    module_sources_by_distribution: dict[str, tuple[_VendorModuleSource, ...]],
    *,
    vendor_root_modules: tuple[str, ...],
) -> dict[str, str]:
    module_sources_by_name = {
        module_source.name: module_source
        for module_sources in module_sources_by_distribution.values()
        for module_source in module_sources
    }
    known_vendor_modules = frozenset(module_sources_by_name)
    current_sources = {
        module_name: module_source.source for module_name, module_source in module_sources_by_name.items()
    }
    current_module_sources_by_distribution = module_sources_by_distribution

    for _ in range(4):
        vendor_nodes, _ = _collect_vendor_nodes(current_module_sources_by_distribution)
        reachable_modules = _compute_reachable_vendor_modules(
            vendor_nodes,
            vendor_root_modules,
            mode=environment.config.mode,
        )
        requested_exports = _collect_requested_vendor_exports(
            analysis,
            current_sources,
            reachable_modules=reachable_modules,
            known_vendor_modules=known_vendor_modules,
        )
        sliced_sources: dict[str, str] = {
            module_name: _slice_vendor_module_source(
                module_source.source,
                module_name,
                requested_exports.get(module_name),
                known_vendor_modules=known_vendor_modules,
                mode=environment.config.mode,
            )
            for module_name, module_source in module_sources_by_name.items()
        }
        referenced_member_names = _collect_referenced_attribute_names(
            analysis,
            sliced_sources,
            reachable_vendor_modules=reachable_modules,
            mode=environment.config.mode,
        )
        project_referenced_member_names = frozenset(_collect_project_attribute_names(analysis, sliced_sources))
        unsafe_base_classes = _collect_unsafe_symbol_pruning_base_classes(sliced_sources)
        next_sources: dict[str, str] = {
            module_name: _prune_vendor_class_members_from_source(
                source,
                module_name,
                referenced_member_names=referenced_member_names,
                project_referenced_member_names=project_referenced_member_names,
                unsafe_base_classes=unsafe_base_classes,
                known_vendor_modules=known_vendor_modules,
                mode=environment.config.mode,
            )
            for module_name, source in sliced_sources.items()
        }
        next_module_sources_by_distribution = _replace_vendor_module_sources(
            module_sources_by_distribution,
            next_sources,
        )
        next_vendor_nodes, _ = _collect_vendor_nodes(next_module_sources_by_distribution)
        next_reachable_modules = _compute_reachable_vendor_modules(
            next_vendor_nodes,
            vendor_root_modules,
            mode=environment.config.mode,
        )
        if next_sources == current_sources and next_reachable_modules == reachable_modules:
            return next_sources
        current_sources = next_sources
        current_module_sources_by_distribution = next_module_sources_by_distribution

    return current_sources


def _collect_referenced_attribute_names(
    analysis: AnalysisResult,
    vendor_sources_by_module: dict[str, str],
    *,
    reachable_vendor_modules: set[str],
    mode: BuildMode,
) -> frozenset[str]:
    del mode
    referenced_names = _collect_project_attribute_names(analysis, vendor_sources_by_module)
    referenced_names.update(
        _collect_vendor_attribute_names(
            analysis,
            vendor_sources_by_module,
            reachable_vendor_modules,
        )
    )
    return frozenset(referenced_names)


def _collect_requested_vendor_exports(
    analysis: AnalysisResult,
    vendor_sources_by_module: dict[str, str],
    *,
    reachable_modules: set[str],
    known_vendor_modules: frozenset[str],
) -> dict[str, _RequestedExports]:
    requested_exports: dict[str, _RequestedExports] = {}

    for module_name in sorted(analysis.graph.reachable):
        node = analysis.graph.nodes.get(module_name)
        if node is None:
            continue
        _collect_requested_exports_from_source(
            node.path.read_text(),
            current_module=module_name,
            known_vendor_modules=known_vendor_modules,
            requested_exports=requested_exports,
        )

    for module_name in sorted(reachable_modules):
        source = vendor_sources_by_module.get(module_name)
        if source is None:
            continue
        _collect_requested_exports_from_source(
            source,
            current_module=module_name,
            known_vendor_modules=known_vendor_modules,
            requested_exports=requested_exports,
        )

    return requested_exports


def _collect_requested_exports_from_source(
    source: str,
    *,
    current_module: str,
    known_vendor_modules: frozenset[str],
    requested_exports: dict[str, _RequestedExports],
) -> None:
    package_name = _module_package_name(current_module, known_vendor_modules)
    collector = _RequestedExportCollector(
        package_name=package_name,
        known_vendor_modules=known_vendor_modules,
        requested_exports=requested_exports,
    )
    collector.visit(ast.parse(source, filename=current_module))
    collector.finalize()


def _slice_vendor_module_source(
    source: str,
    module_name: str,
    requested: _RequestedExports | None,
    *,
    known_vendor_modules: frozenset[str],
    mode: BuildMode,
) -> str:
    if requested is not None and requested.wildcard:
        return source

    requested_names: set[str] = set() if requested is None else set(requested.names)
    explicit_exports = _module_explicit_export_names(source, module_name)
    if explicit_exports:
        requested_names.update(explicit_exports)
        requested_names.add("__getattr__")
    statement_infos = _collect_module_statement_infos(
        source,
        module_name,
        known_vendor_modules=known_vendor_modules,
        mode=mode,
    )
    if not statement_infos:
        return source

    provider_by_name: dict[str, set[int]] = {}
    for index, statement_info in enumerate(statement_infos):
        for name in statement_info.provided_names:
            provider_by_name.setdefault(name, set()).add(index)

    kept_statements: set[int] = set()
    needed_names = set(requested_names)
    pending_names = list(requested_names)

    for index, statement_info in enumerate(statement_infos):
        if not statement_info.droppable:
            kept_statements.add(index)
            for name in statement_info.used_names:
                if name not in needed_names:
                    needed_names.add(name)
                    pending_names.append(name)

    while pending_names:
        name = pending_names.pop()
        for provider_index in provider_by_name.get(name, ()):
            if provider_index in kept_statements:
                continue
            kept_statements.add(provider_index)
            for used_name in statement_infos[provider_index].used_names:
                if used_name not in needed_names:
                    needed_names.add(used_name)
                    pending_names.append(used_name)

    return _rewrite_sliced_module_source(
        source,
        statement_infos,
        kept_statements=kept_statements,
        needed_names=needed_names,
    )


def _prune_vendor_class_members_from_source(
    source: str,
    module_name: str,
    *,
    referenced_member_names: frozenset[str],
    project_referenced_member_names: frozenset[str],
    unsafe_base_classes: frozenset[str],
    known_vendor_modules: frozenset[str],
    mode: BuildMode,
) -> str:
    if mode != "aggressive":
        return source
    barrier_scopes = symbol_pruning_barrier_scopes(source)
    if "module" in barrier_scopes:
        return source

    tree = ast.parse(source, filename=module_name)
    import_bindings = _top_level_import_bindings(
        tree,
        module_name,
        known_vendor_modules=known_vendor_modules,
    )
    lines = source.splitlines(keepends=True)
    replacements: list[tuple[int, int, str | None]] = []
    all_referenced_member_names = frozenset(set(referenced_member_names) | set(project_referenced_member_names))

    for statement in tree.body:
        if not isinstance(statement, ast.ClassDef):
            continue
        replacements.extend(
            _collect_class_member_replacements(
                statement,
                lines,
                referenced_member_names=all_referenced_member_names,
                barrier_scopes=barrier_scopes,
                current_module=module_name,
                import_bindings=import_bindings,
                unsafe_base_classes=unsafe_base_classes,
                mode=mode,
            )
        )

    if not replacements:
        return source

    for start_line, end_line, replacement in sorted(replacements, reverse=True):
        lines[start_line - 1 : end_line] = [replacement] if replacement is not None else []

    return "".join(lines)


def _collect_class_member_replacements(
    class_node: ast.ClassDef,
    lines: list[str],
    *,
    referenced_member_names: frozenset[str],
    barrier_scopes: frozenset[str],
    current_module: str,
    import_bindings: dict[str, _ImportBinding],
    unsafe_base_classes: frozenset[str],
    mode: BuildMode,
) -> list[tuple[int, int, str | None]]:
    if _is_unsafe_class_for_member_pruning(
        class_node,
        barrier_scopes=barrier_scopes,
        current_module=current_module,
        import_bindings=import_bindings,
        unsafe_base_classes=unsafe_base_classes,
    ):
        return []

    member_infos = _collect_class_member_infos(class_node, lines, mode=mode)
    if not member_infos:
        return []
    kept_members = _resolve_kept_class_member_indexes(
        member_infos,
        referenced_member_names=referenced_member_names,
    )

    replacements: list[tuple[int, int, str | None]] = []
    removed_indexes = [
        index for index, member_info in enumerate(member_infos) if member_info.droppable and index not in kept_members
    ]
    if not removed_indexes:
        return []

    kept_any = any(index in kept_members for index in range(len(member_infos)))
    pass_replacement_index = removed_indexes[-1] if not kept_any else None

    for index in removed_indexes:
        member_info = member_infos[index]
        replacement: str | None = None
        if pass_replacement_index == index:
            indent = _statement_indent(lines, member_info.start_line)
            replacement = f"{indent}pass\n"
        replacements.append((member_info.start_line, member_info.end_line, replacement))

    return replacements


def _resolve_kept_class_member_indexes(
    member_infos: tuple[_ClassMemberInfo, ...],
    *,
    referenced_member_names: frozenset[str],
) -> set[int]:
    provider_by_name: dict[str, set[int]] = {}
    for index, member_info in enumerate(member_infos):
        for name in member_info.provided_names:
            provider_by_name.setdefault(name, set()).add(index)

    kept_members: set[int] = set()
    needed_names: set[str] = set()
    pending_names: list[str] = []

    for index, member_info in enumerate(member_infos):
        if not member_info.droppable:
            kept_members.add(index)
            for used_name in member_info.used_names:
                if used_name not in needed_names:
                    needed_names.add(used_name)
                    pending_names.append(used_name)
            continue

        if any(
            name in referenced_member_names or _is_special_class_member_name(name)
            for name in member_info.provided_names
        ):
            kept_members.add(index)
            for name in member_info.provided_names:
                if name not in needed_names:
                    needed_names.add(name)
                    pending_names.append(name)
            for used_name in member_info.used_names:
                if used_name not in needed_names:
                    needed_names.add(used_name)
                    pending_names.append(used_name)

    while pending_names:
        name = pending_names.pop()
        for provider_index in provider_by_name.get(name, ()):
            if provider_index in kept_members:
                continue
            kept_members.add(provider_index)
            for used_name in member_infos[provider_index].used_names:
                if used_name not in needed_names:
                    needed_names.add(used_name)
                    pending_names.append(used_name)

    return kept_members


def _collect_class_member_infos(
    class_node: ast.ClassDef,
    lines: list[str],
    *,
    mode: BuildMode,
) -> tuple[_ClassMemberInfo, ...]:
    infos: list[_ClassMemberInfo] = []

    for statement in class_node.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            usage_collector = _ClassMemberUsageCollector(class_name=class_node.name)
            usage_collector.visit(statement)
            infos.append(
                _ClassMemberInfo(
                    node=statement,
                    start_line=_statement_start_line(statement),
                    end_line=statement.end_lineno or statement.lineno,
                    provided_names={statement.name},
                    used_names=usage_collector.names,
                    droppable=_is_droppable_class_member(statement, mode=mode),
                )
            )
            continue

        if isinstance(statement, ast.Assign):
            provided_names = _bound_names_from_targets(statement.targets)
            infos.append(
                _ClassMemberInfo(
                    node=statement,
                    start_line=statement.lineno,
                    end_line=statement.end_lineno or statement.lineno,
                    provided_names=provided_names,
                    used_names=_collect_loaded_names(statement.value),
                    droppable=False,
                )
            )
            continue

        if isinstance(statement, ast.AnnAssign):
            provided_names = _bound_names_from_target(statement.target)
            infos.append(
                _ClassMemberInfo(
                    node=statement,
                    start_line=statement.lineno,
                    end_line=statement.end_lineno or statement.lineno,
                    provided_names=provided_names,
                    used_names=set() if statement.value is None else _collect_loaded_names(statement.value),
                    droppable=False,
                )
            )
            continue

        infos.append(
            _ClassMemberInfo(
                node=statement,
                start_line=_statement_start_line(statement),
                end_line=statement.end_lineno or statement.lineno,
                droppable=False,
            )
        )

    return tuple(infos)


def _is_droppable_class_member(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    mode: BuildMode,
) -> bool:
    if not _has_strippable_method_body(node):
        return False
    if _is_special_class_member_name(node.name):
        return False
    if mode != "aggressive" and not _matches_method_pattern(node.name, _CLASS_MEMBER_METHOD_PATTERNS):
        return False
    return all(_is_allowed_class_member_decorator(decorator) for decorator in node.decorator_list)


def _is_allowed_class_member_decorator(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"property", "classmethod", "staticmethod", "cached_property"}
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in {"functools", "typing"}:
            return node.attr in {"cached_property", "final", "override"}
        return node.attr in {"setter", "deleter", "getter"}
    return False


def _is_special_class_member_name(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_unsafe_class_for_member_pruning(
    node: ast.ClassDef,
    *,
    barrier_scopes: frozenset[str] = frozenset(),
    current_module: str = "",
    import_bindings: dict[str, _ImportBinding] | None = None,
    unsafe_base_classes: frozenset[str] = frozenset(),
) -> bool:
    if _is_protocol_class(node):
        return True
    if node.decorator_list:
        return True
    if node.name in barrier_scopes:
        return True
    if unsafe_base_classes and any(
        candidate in unsafe_base_classes
        for candidate in _resolved_base_class_names(
            node,
            current_module=current_module,
            import_bindings={} if import_bindings is None else import_bindings,
        )
    ):
        return True
    if _class_has_dynamic_member_lookup(node):
        return True
    special_base_names = {"NamedTuple", "TypedDict", "Enum", "IntEnum", "StrEnum"}
    for base in node.bases:
        base_name = _base_name(base)
        if base_name is None:
            continue
        if any(base_name == candidate or base_name.endswith(f".{candidate}") for candidate in special_base_names):
            return True
    return False


def _collect_unsafe_symbol_pruning_base_classes(
    vendor_sources_by_module: dict[str, str],
) -> frozenset[str]:
    unsafe: set[str] = set()
    for module_name, source in vendor_sources_by_module.items():
        barrier_scopes = symbol_pruning_barrier_scopes(source)
        if not barrier_scopes:
            continue
        tree = ast.parse(source, filename=module_name)
        module_class_names = {statement.name for statement in tree.body if isinstance(statement, ast.ClassDef)}
        for class_name in sorted(module_class_names & set(barrier_scopes)):
            unsafe.add(f"{module_name}.{class_name}")
    return frozenset(unsafe)


def _resolved_base_class_names(
    node: ast.ClassDef,
    *,
    current_module: str,
    import_bindings: dict[str, _ImportBinding],
) -> frozenset[str]:
    resolved: set[str] = set()
    for base in node.bases:
        resolved.update(
            _resolved_base_name(
                base,
                current_module=current_module,
                import_bindings=import_bindings,
            )
        )
    return frozenset(resolved)


def _resolved_base_name(
    node: ast.AST,
    *,
    current_module: str,
    import_bindings: dict[str, _ImportBinding],
) -> frozenset[str]:
    if isinstance(node, ast.Subscript):
        return _resolved_base_name(
            node.value,
            current_module=current_module,
            import_bindings=import_bindings,
        )
    if isinstance(node, ast.Name):
        binding = import_bindings.get(node.id)
        if binding is None:
            return frozenset({f"{current_module}.{node.id}"})
        if binding.imported_name is not None:
            return frozenset({f"{binding.module_name}.{binding.imported_name}"})
        return frozenset({binding.module_name})
    if isinstance(node, ast.Attribute):
        parts = _attribute_parts(node)
        if not parts:
            return frozenset()
        binding = import_bindings.get(parts[0])
        if binding is None:
            return frozenset({".".join(parts)})
        if binding.imported_name is not None:
            base_name = f"{binding.module_name}.{binding.imported_name}"
        else:
            base_name = binding.module_name
        if len(parts) == 1:
            return frozenset({base_name})
        return frozenset({f"{base_name}.{'.'.join(parts[1:])}"})
    return frozenset()


def _attribute_parts(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        prefix = _attribute_parts(node.value)
        if not prefix:
            return ()
        return (*prefix, node.attr)
    return ()


def _class_has_dynamic_member_lookup(node: ast.ClassDef) -> bool:
    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.has_dynamic_lookup = False

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if self.has_dynamic_lookup:
                return
            if node.attr == "__dict__" and _is_class_member_lookup_target(node.value, class_name=node_class_name):
                self.has_dynamic_lookup = True
                return
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            if self.has_dynamic_lookup:
                return
            name = _call_name(node.func)
            if name in {"getattr", "hasattr", "setattr", "delattr"} and _is_class_member_lookup_target(
                _positional_argument(node, 0),
                class_name=node_class_name,
            ):
                if _constant_string_argument(node, 1) is None:
                    self.has_dynamic_lookup = True
                    return
            if name in {"vars", "dir", "inspect.getmembers"} and _is_class_member_lookup_target(
                _positional_argument(node, 0),
                class_name=node_class_name,
            ):
                self.has_dynamic_lookup = True
                return
            self.generic_visit(node)

    node_class_name = node.name
    visitor = Visitor()
    visitor.visit(node)
    return visitor.has_dynamic_lookup


def _base_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return _call_name(node)


def _collect_loaded_names(node: ast.AST) -> set[str]:
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                names.add(node.id)

    Visitor().visit(node)
    return names


def _collect_module_statement_infos(
    source: str,
    module_name: str,
    *,
    known_vendor_modules: frozenset[str],
    mode: BuildMode,
) -> tuple[_ModuleStatementInfo, ...]:
    tree = ast.parse(source, filename=module_name)
    import_bindings = _top_level_import_bindings(
        tree,
        module_name,
        known_vendor_modules=known_vendor_modules,
    )
    infos: list[_ModuleStatementInfo] = []
    for statement in tree.body:
        usage_collector = _TopLevelStatementUsageCollector(import_bindings)
        runtime_branch = _runtime_branch_for_if(statement)
        if runtime_branch is None:
            usage_collector.visit(statement)
        else:
            for branch_statement in runtime_branch:
                usage_collector.visit(branch_statement)
        infos.append(
            _ModuleStatementInfo(
                node=statement,
                start_line=_statement_start_line(statement),
                end_line=statement.end_lineno or statement.lineno,
                provided_names=_top_level_bound_names(
                    statement,
                    module_name,
                    known_vendor_modules=known_vendor_modules,
                ),
                used_names=usage_collector.names,
                droppable=_is_droppable_top_level_statement(statement, mode=mode),
            )
        )
    return tuple(infos)


def _statement_start_line(statement: ast.stmt) -> int:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and statement.decorator_list:
        return min(decorator.lineno for decorator in statement.decorator_list)
    return statement.lineno


def _top_level_import_bindings(
    tree: ast.Module,
    module_name: str,
    *,
    known_vendor_modules: frozenset[str],
) -> dict[str, _ImportBinding]:
    package_name = _module_package_name(module_name, known_vendor_modules)
    bindings: dict[str, _ImportBinding] = {}

    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bound_name = alias.asname or alias.name.partition(".")[0]
                bindings[bound_name] = _ImportBinding(module_name=alias.name)
        elif isinstance(statement, ast.ImportFrom):
            resolved_module = _resolve_relative_import(package_name, statement.module, statement.level)
            if not resolved_module:
                continue
            for alias in statement.names:
                if alias.name == "*":
                    continue
                bound_name = alias.asname or alias.name
                bindings[bound_name] = _resolve_import_binding(
                    resolved_module,
                    alias.name,
                    known_vendor_modules=known_vendor_modules,
                )

    return bindings


def _rewrite_sliced_module_source(
    source: str,
    statement_infos: tuple[_ModuleStatementInfo, ...],
    *,
    kept_statements: set[int],
    needed_names: set[str],
) -> str:
    lines = source.splitlines(keepends=True)

    for index in range(len(statement_infos) - 1, -1, -1):
        statement_info = statement_infos[index]
        if index not in kept_statements:
            lines[statement_info.start_line - 1 : statement_info.end_line] = []
            continue

        if (
            isinstance(statement_info.node, (ast.Import, ast.ImportFrom))
            or _runtime_branch_for_if(statement_info.node) is not None
        ):
            replacement = _rewritten_statement_text(statement_info.node, needed_names=needed_names)
            if replacement is None:
                lines[statement_info.start_line - 1 : statement_info.end_line] = []
                continue
            if replacement == _original_statement_text(source, statement_info):
                continue
            lines[statement_info.start_line - 1 : statement_info.end_line] = [replacement]

    return "".join(lines)


def _rewritten_statement_text(node: ast.stmt, *, needed_names: set[str]) -> str | None:
    runtime_branch = _runtime_branch_for_if(node)
    if runtime_branch is not None:
        if not runtime_branch:
            return None
        return "".join(f"{ast.unparse(branch_statement)}\n" for branch_statement in runtime_branch)

    if isinstance(node, ast.Import):
        aliases = [
            alias for alias in node.names if _bound_name_for_import_alias(alias, import_from=False) in needed_names
        ]
        if not aliases:
            return None
        return f"{ast.unparse(ast.Import(names=aliases))}\n"

    if isinstance(node, ast.ImportFrom):
        if node.module == "__future__":
            return f"{ast.unparse(node)}\n"
        aliases = [
            alias
            for alias in node.names
            if alias.name == "*" or _bound_name_for_import_alias(alias, import_from=True) in needed_names
        ]
        if not aliases:
            return None
        return f"{ast.unparse(ast.ImportFrom(module=node.module, names=aliases, level=node.level))}\n"

    return f"{ast.unparse(node)}\n"


def _original_statement_text(source: str, statement_info: _ModuleStatementInfo) -> str:
    return "".join(source.splitlines(keepends=True)[statement_info.start_line - 1 : statement_info.end_line])


def _top_level_bound_names(
    statement: ast.stmt,
    module_name: str,
    *,
    known_vendor_modules: frozenset[str],
) -> set[str]:
    package_name = _module_package_name(module_name, known_vendor_modules)
    runtime_branch = _runtime_branch_for_if(statement)

    if runtime_branch is not None:
        names: set[str] = set()
        for branch_statement in runtime_branch:
            names.update(
                _top_level_bound_names(
                    branch_statement,
                    module_name,
                    known_vendor_modules=known_vendor_modules,
                )
            )
        return names

    if isinstance(statement, ast.Import):
        return {_bound_name_for_import_alias(alias, import_from=False) for alias in statement.names}

    if isinstance(statement, ast.ImportFrom):
        resolved_module = _resolve_relative_import(package_name, statement.module, statement.level)
        if not resolved_module:
            return set()
        names: set[str] = set()
        for alias in statement.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname or alias.name
            if bound_name:
                names.add(bound_name)
        return names

    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}

    if isinstance(statement, ast.Assign):
        return _bound_names_from_targets(statement.targets)

    if isinstance(statement, ast.AnnAssign):
        return _bound_names_from_target(statement.target)

    return set()


def _bound_name_for_import_alias(alias: ast.alias, *, import_from: bool) -> str:
    if alias.asname is not None:
        return alias.asname
    if import_from:
        return alias.name
    return alias.name.partition(".")[0]


def _bound_names_from_targets(targets: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for target in targets:
        names.update(_bound_names_from_target(target))
    return names


def _bound_names_from_target(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in target.elts:
            names.update(_bound_names_from_target(item))
        return names
    return set()


def _is_droppable_top_level_statement(node: ast.stmt, *, mode: BuildMode) -> bool:
    runtime_branch = _runtime_branch_for_if(node)
    if runtime_branch is not None:
        return all(
            _is_droppable_top_level_statement(branch_statement, mode=mode) for branch_statement in runtime_branch
        )
    if isinstance(node, ast.Import):
        return True
    if isinstance(node, ast.ImportFrom):
        return node.module != "__future__"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return True
    if isinstance(node, ast.Assign):
        targets = _bound_names_from_targets(node.targets)
        if not targets:
            return False
        if mode == "aggressive":
            return True
        return _is_side_effect_free_expression(node.value)
    if isinstance(node, ast.AnnAssign):
        targets = _bound_names_from_target(node.target)
        if not targets:
            return False
        if node.value is None:
            return True
        if mode == "aggressive":
            return True
        return _is_side_effect_free_expression(node.value)
    return False


def _module_package_name(module_name: str, known_modules: frozenset[str]) -> str:
    package_prefix = f"{module_name}."
    if any(candidate.startswith(package_prefix) for candidate in known_modules):
        return module_name
    return module_name.rpartition(".")[0]


def _module_explicit_export_names(source: str, module_name: str) -> set[str]:
    tree = ast.parse(source, filename=module_name)
    explicit_exports: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            target_names = _bound_names_from_targets(statement.targets)
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target_names = _bound_names_from_target(statement.target)
            value = statement.value
        else:
            continue
        if value is None:
            continue
        export_targets = {
            name for name in target_names if name == "__all__" or name == "exported" or name.endswith("_exported")
        }
        if not export_targets:
            continue
        export_names = _string_sequence_literal(value)
        if export_names is not None:
            explicit_exports.update(export_names)
    return explicit_exports


def _string_sequence_literal(node: ast.AST) -> set[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    names: set[str] = set()
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        names.add(element.value)
    return names


def _runtime_branch_for_if(node: ast.stmt) -> list[ast.stmt] | None:
    from .collectors import _is_type_checking_guard, _static_truth_value

    if not isinstance(node, ast.If):
        return None
    if _is_type_checking_guard(node.test):
        return node.orelse
    static_truth = _static_truth_value(node.test)
    if static_truth is True:
        return node.body
    if static_truth is False:
        return node.orelse
    return None


def _is_side_effect_free_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return True
    if isinstance(node, ast.Attribute):
        return _is_side_effect_free_expression(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_side_effect_free_expression(element) for element in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _is_side_effect_free_expression(key)) and _is_side_effect_free_expression(value)
            for key, value in zip(node.keys, node.values, strict=False)
        )
    if isinstance(node, ast.UnaryOp):
        return _is_side_effect_free_expression(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_side_effect_free_expression(node.left) and _is_side_effect_free_expression(node.right)
    return False


def _collect_protected_method_names(
    analysis: AnalysisResult,
    vendor_sources_by_module: dict[str, str],
    reachable_vendor_modules: set[str],
    *,
    method_patterns: tuple[str, ...],
) -> frozenset[str]:
    del reachable_vendor_modules
    referenced_names = _collect_project_attribute_names(analysis, vendor_sources_by_module)
    protected = {name for name in referenced_names if _matches_method_pattern(name, method_patterns)}
    return frozenset(protected)


def _collect_project_attribute_names(
    analysis: AnalysisResult,
    vendor_sources_by_module: dict[str, str],
) -> set[str]:
    known_modules = frozenset(set(analysis.graph.nodes) | set(vendor_sources_by_module))
    names: set[str] = set()
    for module_name in sorted(analysis.graph.reachable):
        node = analysis.graph.nodes.get(module_name)
        if node is None:
            continue
        names.update(
            _collect_used_attribute_names(
                node.path.read_text(),
                current_module=module_name,
                known_modules=known_modules,
            )
        )
    return names


def _collect_vendor_attribute_names(
    analysis: AnalysisResult,
    vendor_sources_by_module: dict[str, str],
    reachable_vendor_modules: set[str],
) -> set[str]:
    known_modules = frozenset(set(analysis.graph.nodes) | set(vendor_sources_by_module))
    names: set[str] = set()
    for module_name in sorted(reachable_vendor_modules):
        source = vendor_sources_by_module.get(module_name)
        if source is None:
            continue
        names.update(
            _collect_used_attribute_names(
                source,
                current_module=module_name,
                known_modules=known_modules,
            )
        )
    return names


def _collect_used_attribute_names(
    source: str,
    *,
    current_module: str,
    known_modules: frozenset[str],
) -> set[str]:
    collector = _UsedAttributeCollector(
        package_name=_module_package_name(current_module, known_modules),
        known_modules=known_modules,
    )
    collector.visit(ast.parse(source, filename=current_module))
    return collector.names


_EXPERIMENTAL_CLASS_PRUNING_SYMBOLS = (
    _collect_class_member_replacements,
    _resolve_kept_class_member_indexes,
    _collect_class_member_infos,
    _is_droppable_class_member,
    _is_allowed_class_member_decorator,
    _is_special_class_member_name,
    _is_unsafe_class_for_member_pruning,
    _class_has_dynamic_member_lookup,
    _base_name,
    _collect_loaded_names,
)
