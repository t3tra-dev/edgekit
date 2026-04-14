from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .ast_support import resolve_relative_import
from .common import module_name_from_relative_path, module_package_name, relative_path_from_root
from .models import ResolvedEnvironment, RiskReport

type BindingKind = Literal["assets", "d1", "durable_object", "kv", "queue", "r2"]

_WORKER_ENTRYPOINT_SYMBOLS = frozenset(
    {
        "edgekit.WorkerEntrypoint",
        "edgekit.worker.WorkerEntrypoint",
        "edgekit.adapters.WSGI",
        "edgekit.adapters.wsgi.WSGI",
        "edgekit.adapters.ASGI",
        "edgekit.adapters.asgi.ASGI",
    }
)
_BINDING_SYMBOLS: dict[str, BindingKind] = {
    "edgekit.bindings.StaticAssets": "assets",
    "edgekit.bindings.assets.StaticAssets": "assets",
    "edgekit.bindings.D1Database": "d1",
    "edgekit.bindings.d1.D1Database": "d1",
    "edgekit.bindings.DurableObjectNamespace": "durable_object",
    "edgekit.bindings.durable_objects.DurableObjectNamespace": "durable_object",
    "edgekit.bindings.KVNamespace": "kv",
    "edgekit.bindings.kv.KVNamespace": "kv",
    "edgekit.bindings.QueueProducer": "queue",
    "edgekit.bindings.queues.QueueProducer": "queue",
    "edgekit.bindings.R2Bucket": "r2",
    "edgekit.bindings.r2.R2Bucket": "r2",
}


@dataclass(slots=True, frozen=True)
class EnvBindingDeclaration:
    name: str
    kind: BindingKind
    annotation: str
    worker_class: str
    env_class: str
    module_name: str
    path: Path
    lineno: int


@dataclass(slots=True)
class _ParsedModule:
    module_name: str
    path: Path
    source_root: Path
    tree: ast.Module
    imports: Mapping[str, str]
    classes: Mapping[str, ast.ClassDef]


def collect_binding_validation_risks(environment: ResolvedEnvironment) -> RiskReport:
    report = RiskReport()
    declarations = _discover_env_binding_declarations(environment, report)
    if not declarations:
        return report

    _check_declared_binding_conflicts(declarations, environment, report)

    configured_by_kind = _configured_binding_names(environment)
    configured_name_kinds: dict[str, set[BindingKind]] = defaultdict(set)
    for kind, names in configured_by_kind.items():
        for name in names:
            configured_name_kinds[name].add(kind)

    for declaration in declarations:
        if declaration.name in configured_by_kind[declaration.kind]:
            continue
        configured_kinds = configured_name_kinds.get(declaration.name)
        relative_path = relative_path_from_root(declaration.path, environment.project_root)
        if configured_kinds:
            actual = ", ".join(sorted(kind for kind in configured_kinds))
            report.add(
                "error",
                f"Env binding '{declaration.name}' is declared as {declaration.kind} but Wrangler config defines {actual}",
                path=relative_path,
                code="binding_kind_mismatch",
            )
            continue
        report.add(
            "error",
            f"Env binding '{declaration.name}' is declared as {declaration.kind} but missing from Wrangler config",
            path=relative_path,
            code="missing_binding",
        )

    assets_config = environment.wrangler_config.get("assets")
    if isinstance(assets_config, Mapping):
        assets_mapping = cast(Mapping[str, object], assets_config)
        directory = assets_mapping.get("directory")
        if isinstance(directory, str):
            assets_dir = (environment.project_root / directory).resolve()
            if not assets_dir.exists():
                report.add(
                    "error",
                    f"Static assets directory does not exist: {directory}",
                    path=str(environment.wrangler_path.relative_to(environment.project_root))
                    if environment.wrangler_path is not None
                    else None,
                    code="missing_assets_directory",
                )

    return report


def _discover_env_binding_declarations(
    environment: ResolvedEnvironment,
    report: RiskReport,
) -> tuple[EnvBindingDeclaration, ...]:
    module_cache: dict[str, _ParsedModule | None] = {}
    entry_relative = environment.entry.relative_to(environment.project_root)
    entry_module = module_name_from_relative_path(entry_relative)
    if entry_module is None:
        return ()
    parsed_entry = _parse_module(environment.project_root, entry_module, module_cache)
    if parsed_entry is None:
        return ()

    declarations: list[EnvBindingDeclaration] = []
    for worker_class, env_reference in _worker_entrypoint_env_references(parsed_entry):
        env_class = _resolve_class_reference(environment.project_root, parsed_entry, env_reference, module_cache)
        if env_class is None:
            report.add(
                "error",
                f"Could not resolve Env declaration '{env_reference}' for worker '{worker_class}'",
                path=relative_path_from_root(parsed_entry.path, environment.project_root),
                code="unresolved_env_declaration",
            )
            continue
        declarations.extend(
            _env_binding_declarations(
                env_class,
                worker_class=worker_class,
                project_root=environment.project_root,
            )
        )

    return tuple(declarations)


def _worker_entrypoint_env_references(parsed_module: _ParsedModule) -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    for class_def in parsed_module.classes.values():
        for base in class_def.bases:
            if not isinstance(base, ast.Subscript):
                continue
            base_name = _resolved_expr_name(base.value, parsed_module.imports, parsed_module.module_name)
            if base_name not in _WORKER_ENTRYPOINT_SYMBOLS:
                continue
            env_reference = _resolved_expr_name(_slice_expr(base), parsed_module.imports, parsed_module.module_name)
            if env_reference is None:
                continue
            references.append((class_def.name, env_reference))
    return tuple(references)


def _env_binding_declarations(
    env_class: _ResolvedClass,
    *,
    worker_class: str,
    project_root: Path,
) -> tuple[EnvBindingDeclaration, ...]:
    declarations: list[EnvBindingDeclaration] = []
    for node in env_class.class_def.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        annotation_name = _resolved_expr_name(node.annotation, env_class.imports, env_class.module_name)
        if annotation_name is None:
            continue
        kind = _binding_kind_for_annotation(annotation_name)
        if kind is None:
            continue
        declarations.append(
            EnvBindingDeclaration(
                name=node.target.id,
                kind=kind,
                annotation=annotation_name,
                worker_class=worker_class,
                env_class=env_class.class_def.name,
                module_name=env_class.module_name,
                path=env_class.path,
                lineno=node.lineno,
            )
        )
    return tuple(declarations)


def _check_declared_binding_conflicts(
    declarations: tuple[EnvBindingDeclaration, ...],
    environment: ResolvedEnvironment,
    report: RiskReport,
) -> None:
    by_name: dict[str, set[BindingKind]] = defaultdict(set)
    first_paths: dict[str, Path] = {}
    for declaration in declarations:
        by_name[declaration.name].add(declaration.kind)
        first_paths.setdefault(declaration.name, declaration.path)

    for binding_name, kinds in sorted(by_name.items()):
        if len(kinds) < 2:
            continue
        report.add(
            "error",
            f"Env binding '{binding_name}' is declared with conflicting kinds: {', '.join(sorted(kinds))}",
            path=relative_path_from_root(first_paths[binding_name], environment.project_root),
            code="binding_kind_conflict",
        )


def _configured_binding_names(environment: ResolvedEnvironment) -> dict[BindingKind, set[str]]:
    wrangler = environment.wrangler_config
    names: dict[BindingKind, set[str]] = {
        "assets": set(),
        "d1": set(),
        "durable_object": set(),
        "kv": set(),
        "queue": set(),
        "r2": set(),
    }

    assets = wrangler.get("assets")
    if isinstance(assets, Mapping):
        assets_mapping = cast(Mapping[str, object], assets)
        binding = assets_mapping.get("binding")
        if isinstance(binding, str):
            names["assets"].add(binding)

    for item in _mapping_sequence(wrangler.get("d1_databases")):
        binding = item.get("binding")
        if isinstance(binding, str):
            names["d1"].add(binding)

    durable_objects = wrangler.get("durable_objects")
    if isinstance(durable_objects, Mapping):
        durable_objects_mapping = cast(Mapping[str, object], durable_objects)
        for item in _mapping_sequence(durable_objects_mapping.get("bindings")):
            binding = item.get("name")
            if isinstance(binding, str):
                names["durable_object"].add(binding)

    for item in _mapping_sequence(wrangler.get("kv_namespaces")):
        binding = item.get("binding")
        if isinstance(binding, str):
            names["kv"].add(binding)

    queues = wrangler.get("queues")
    if isinstance(queues, Mapping):
        queues_mapping = cast(Mapping[str, object], queues)
        for item in _mapping_sequence(queues_mapping.get("producers")):
            binding = item.get("binding")
            if isinstance(binding, str):
                names["queue"].add(binding)

    for item in _mapping_sequence(wrangler.get("r2_buckets")):
        binding = item.get("binding")
        if isinstance(binding, str):
            names["r2"].add(binding)

    return names


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    items: list[Mapping[str, object]] = []
    for item in cast(Sequence[object], value):
        if isinstance(item, Mapping):
            items.append(cast(Mapping[str, object], item))
    return tuple(items)


@dataclass(slots=True, frozen=True)
class _ResolvedClass:
    module_name: str
    path: Path
    imports: Mapping[str, str]
    class_def: ast.ClassDef


def _resolve_class_reference(
    project_root: Path,
    current_module: _ParsedModule,
    reference: str,
    module_cache: dict[str, _ParsedModule | None],
) -> _ResolvedClass | None:
    if "." in reference:
        module_name, _, class_name = reference.rpartition(".")
    else:
        module_name = current_module.module_name
        class_name = reference
    parsed_module = _parse_module(project_root, module_name, module_cache)
    if parsed_module is None:
        return None
    class_def = parsed_module.classes.get(class_name)
    if class_def is None:
        return None
    return _ResolvedClass(
        module_name=parsed_module.module_name,
        path=parsed_module.path,
        imports=parsed_module.imports,
        class_def=class_def,
    )


def _parse_module(
    project_root: Path,
    module_name: str,
    module_cache: dict[str, _ParsedModule | None],
) -> _ParsedModule | None:
    cached = module_cache.get(module_name)
    if cached is not None or module_name in module_cache:
        return cached

    resolved_module = _module_path_from_name(project_root, module_name)
    if resolved_module is None:
        module_cache[module_name] = None
        return None
    module_path, source_root = resolved_module
    tree = ast.parse(module_path.read_text(), filename=str(module_path))
    imports = _module_imports(tree, module_name, module_path.relative_to(source_root))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    parsed = _ParsedModule(
        module_name=module_name,
        path=module_path,
        source_root=source_root,
        tree=tree,
        imports=imports,
        classes=classes,
    )
    module_cache[module_name] = parsed
    return parsed


def _module_path_from_name(project_root: Path, module_name: str) -> tuple[Path, Path] | None:
    relative = Path(*module_name.split("."))
    for source_root in _source_roots(project_root):
        module_path = source_root / relative.with_suffix(".py")
        if module_path.exists():
            return module_path, source_root
        package_init = source_root / relative / "__init__.py"
        if package_init.exists():
            return package_init, source_root
    return None


def _source_roots(project_root: Path) -> tuple[Path, ...]:
    src_root = project_root / "src"
    if src_root.exists():
        return (src_root, project_root)
    return (project_root,)


def _module_imports(tree: ast.Module, module_name: str, relative_path: Path) -> dict[str, str]:
    imports: dict[str, str] = {}
    package_name = module_package_name(module_name, relative_path=relative_path)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                imports[local_name] = alias.name if alias.asname else local_name
        elif isinstance(node, ast.ImportFrom):
            resolved_module = resolve_relative_import(package_name, node.module, node.level)
            if not resolved_module:
                continue
            for alias in node.names:
                local_name = alias.asname or alias.name
                imports[local_name] = f"{resolved_module}.{alias.name}"
    return imports


def _resolved_expr_name(
    node: ast.AST,
    imports: Mapping[str, str],
    current_module_name: str,
) -> str | None:
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _resolved_expr_name(node.value, imports, current_module_name)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return _resolved_expr_name(node.value, imports, current_module_name)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Tuple):
        return None
    return None


def _binding_kind_for_annotation(annotation_name: str) -> BindingKind | None:
    if annotation_name in _BINDING_SYMBOLS:
        return _BINDING_SYMBOLS[annotation_name]
    normalized = annotation_name.rsplit(".", 1)[-1]
    for symbol_name, kind in _BINDING_SYMBOLS.items():
        if symbol_name.endswith(f".{normalized}"):
            return kind
    return None


def _slice_expr(node: ast.Subscript) -> ast.AST:
    return cast(ast.AST, node.slice)
