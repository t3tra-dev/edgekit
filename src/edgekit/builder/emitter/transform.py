# pyright: reportPrivateUsage=false, reportUnusedFunction=false
from __future__ import annotations

import ast
import fnmatch
import io
import re
import tokenize

from ..ast_support import is_docstring_expr as _is_docstring_expr
from .types import _STRIPPED_METHOD_MESSAGE


def _strip_docstrings_from_source(source: str) -> str:
    tree = ast.parse(source)
    replacements = _collect_docstring_spans(tree, source.splitlines(keepends=True))
    if not replacements:
        return source

    lines = source.splitlines(keepends=True)
    for start_line, end_line, replacement in sorted(replacements, reverse=True):
        lines[start_line - 1 : end_line] = [replacement] if replacement is not None else []
    return "".join(lines)


def _strip_comments_from_source(source: str) -> str:
    filtered_tokens: list[tokenize.TokenInfo] = []
    for token_info in tokenize.generate_tokens(io.StringIO(source).readline):
        if token_info.type == tokenize.COMMENT and not _should_keep_comment(token_info):
            continue
        filtered_tokens.append(token_info)
    return tokenize.untokenize(filtered_tokens)


def _should_keep_comment(token_info: tokenize.TokenInfo) -> bool:
    line_number = token_info.start[0]
    text = token_info.string
    if line_number == 1 and text.startswith("#!"):
        return True
    return line_number <= 2 and "coding" in text


def _collect_docstring_spans(
    tree: ast.AST,
    lines: list[str],
) -> list[tuple[int, int, str | None]]:
    replacements: list[tuple[int, int, str | None]] = []

    def visit_body(
        body: list[ast.stmt],
        *,
        owner: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None = None,
    ) -> None:
        if not body:
            return
        first = body[0]
        if _is_docstring_expr(first):
            start_line = first.lineno
            end_line = first.end_lineno or first.lineno
            if owner is None or isinstance(owner, ast.Module):
                replacements.append((start_line, end_line, None))
            elif start_line > owner.lineno:
                replacement: str | None = None
                if len(body) == 1:
                    indent = _statement_indent(lines, start_line)
                    replacement = f"{indent}pass\n"
                replacements.append((start_line, end_line, replacement))
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit_body(statement.body, owner=statement)
            elif isinstance(statement, ast.If):
                visit_body(statement.body)
                visit_body(statement.orelse)
            elif isinstance(statement, ast.Try):
                visit_body(statement.body)
                for handler in statement.handlers:
                    visit_body(handler.body)
                visit_body(statement.orelse)
                visit_body(statement.finalbody)
            elif isinstance(statement, ast.With):
                visit_body(statement.body)
            elif isinstance(statement, ast.AsyncWith):
                visit_body(statement.body)
            elif isinstance(statement, ast.For):
                visit_body(statement.body)
                visit_body(statement.orelse)
            elif isinstance(statement, ast.AsyncFor):
                visit_body(statement.body)
                visit_body(statement.orelse)
            elif isinstance(statement, ast.While):
                visit_body(statement.body)
                visit_body(statement.orelse)
            elif isinstance(statement, ast.Match):
                for case in statement.cases:
                    visit_body(case.body)

    if isinstance(tree, ast.Module):
        visit_body(tree.body, owner=tree)
    return replacements


def _statement_indent(lines: list[str], start_line: int) -> str:
    indent_match = re.match(r"\s*", lines[start_line - 1])
    if indent_match is None:
        return ""
    return indent_match.group(0)


def _strip_instance_methods_from_source(
    source: str,
    *,
    method_patterns: tuple[str, ...],
    protected_method_names: frozenset[str],
) -> str:
    if not method_patterns:
        return source

    tree = ast.parse(source)
    replacements = _collect_stripped_method_spans(
        tree,
        source.splitlines(keepends=True),
        method_patterns,
        protected_method_names,
    )
    if not replacements:
        return source

    lines = source.splitlines(keepends=True)
    for start_line, end_line, indent, method_name in sorted(replacements, reverse=True):
        replacement = f"{indent}raise RuntimeError({_stripped_method_message(method_name)!r})\n"
        lines[start_line - 1 : end_line] = [replacement]
    return "".join(lines)


def _collect_stripped_method_spans(
    tree: ast.AST,
    lines: list[str],
    method_patterns: tuple[str, ...],
    protected_method_names: frozenset[str],
) -> list[tuple[int, int, str, str]]:
    replacements: list[tuple[int, int, str, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.class_stack: list[bool] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.class_stack.append(_is_protocol_class(node))
            for child in node.body:
                self.visit(child)
            self.class_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if (
                self.class_stack
                and not self.class_stack[-1]
                and _matches_method_pattern(node.name, method_patterns)
                and node.name not in protected_method_names
                and _has_strippable_method_body(node)
            ):
                replacements.append(_method_body_span(node, lines))

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if (
                self.class_stack
                and not self.class_stack[-1]
                and _matches_method_pattern(node.name, method_patterns)
                and node.name not in protected_method_names
                and _has_strippable_method_body(node)
            ):
                replacements.append(_method_body_span(node, lines))

    Visitor().visit(tree)
    return replacements


def _matches_method_pattern(method_name: str, method_patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(method_name, pattern) for pattern in method_patterns)


def _method_body_span(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
) -> tuple[int, int, str, str]:
    if not node.body:
        raise ValueError(f"Function body is empty: {node.name}")
    start_line = node.body[0].lineno
    end_line = node.body[-1].end_lineno or node.body[-1].lineno
    indent_match = re.match(r"\s*", lines[start_line - 1])
    if indent_match is None:
        raise ValueError(f"Could not determine indentation for: {node.name}")
    indent = indent_match.group(0)
    return start_line, end_line, indent, node.name


def _stripped_method_message(method_name: str) -> str:
    return f"{_STRIPPED_METHOD_MESSAGE} ({method_name})"


def _is_protocol_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
    return False


def _has_strippable_method_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if len(node.body) != 1:
        return True
    only_statement = node.body[0]
    if isinstance(only_statement, ast.Pass):
        return False
    return not (
        isinstance(only_statement, ast.Expr)
        and isinstance(only_statement.value, ast.Constant)
        and only_statement.value.value is Ellipsis
    )


def _remove_unused_imports_after_strip(original: str, transformed: str) -> str:
    if original == transformed:
        return transformed

    original_tree = ast.parse(original)
    transformed_tree = ast.parse(transformed)
    original_used = _used_names(original_tree)
    transformed_used = _used_names(transformed_tree)
    removable_ranges: list[tuple[int, int]] = []

    for node in transformed_tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        bound_names = _import_bound_names(node)
        if not bound_names:
            continue
        if all(name in original_used and name not in transformed_used for name in bound_names):
            removable_ranges.append((node.lineno, node.end_lineno or node.lineno))

    if not removable_ranges:
        return transformed

    lines = transformed.splitlines(keepends=True)
    for start_line, end_line in sorted(removable_ranges, reverse=True):
        lines[start_line - 1 : end_line] = []
    return "".join(lines)


def _compact_python_source_text(source: str) -> str:
    compacted_lines: list[str] = []
    blank_run = 0

    for raw_line in source.splitlines(keepends=True):
        line = raw_line.rstrip()
        if not line:
            blank_run += 1
            if blank_run > 1:
                continue
            compacted_lines.append("\n")
            continue

        blank_run = 0
        compacted_lines.append(f"{line}\n")

    while compacted_lines and compacted_lines[0] == "\n":
        compacted_lines.pop(0)

    if not compacted_lines:
        return ""

    if compacted_lines[-1] != "\n" and not compacted_lines[-1].endswith("\n"):
        compacted_lines[-1] = f"{compacted_lines[-1]}\n"

    return "".join(compacted_lines)


def _used_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                names.add(node.id)

    Visitor().visit(tree)
    return names


def _import_bound_names(node: ast.Import | ast.ImportFrom) -> tuple[str, ...]:
    bound: list[str] = []
    for alias in node.names:
        if alias.asname is not None:
            bound.append(alias.asname)
            continue
        head = alias.name.partition(".")[0]
        if head:
            bound.append(head)
    return tuple(bound)
