"""AST-based codebase analyzer — scans SharanBot's own code for structure analysis.

Extracts classes, functions, imports, dependencies, and call graphs
using Python AST. Used by the self-build system to understand what
exists before making modifications.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    args: list[str]
    decorators: list[str] = field(default_factory=list)
    docstring: str = ""
    is_async: bool = False
    complexity: int = 1  # McCabe-like estimate


@dataclass
class ClassInfo:
    name: str
    lineno: int
    end_lineno: int
    bases: list[str]
    methods: list[FunctionInfo] = field(default_factory=list)
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    path: str
    imports: list[str] = field(default_factory=list)
    from_imports: dict[str, list[str]] = field(default_factory=dict)
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    global_vars: list[str] = field(default_factory=list)
    docstring: str = ""
    lines: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CodebaseMap:
    """Complete map of a Python codebase."""
    root: str
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    dependency_graph: dict[str, list[str]] = field(default_factory=dict)
    total_lines: int = 0
    total_classes: int = 0
    total_functions: int = 0

    def get_module(self, module_path: str) -> ModuleInfo | None:
        return self.modules.get(module_path)

    def find_class(self, class_name: str) -> list[tuple[str, ClassInfo]]:
        """Find all classes matching name across modules."""
        results = []
        for mod_path, mod in self.modules.items():
            for cls in mod.classes:
                if cls.name == class_name:
                    results.append((mod_path, cls))
        return results

    def find_function(self, func_name: str) -> list[tuple[str, FunctionInfo]]:
        """Find all top-level functions matching name."""
        results = []
        for mod_path, mod in self.modules.items():
            for fn in mod.functions:
                if fn.name == func_name:
                    results.append((mod_path, fn))
        return results

    def get_summary(self) -> str:
        """Human-readable codebase summary."""
        lines = [
            f"Codebase: {self.root}",
            f"  Modules: {len(self.modules)}",
            f"  Classes: {self.total_classes}",
            f"  Functions: {self.total_functions}",
            f"  Total lines: {self.total_lines}",
        ]
        # Top modules by size
        by_size = sorted(self.modules.items(), key=lambda x: x[1].lines, reverse=True)
        lines.append("  Largest modules:")
        for path, mod in by_size[:10]:
            lines.append(f"    {path}: {mod.lines} lines, {len(mod.classes)} classes")
        return "\n".join(lines)


class CodebaseAnalyzer:
    """AST-based Python codebase scanner."""

    SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".tox", ".venv",
                 "venv", "env", ".eggs", "*.egg-info", "dist", "build"}

    def __init__(self, root: Path | None = None):
        self._root = root or Path("sharanbot")

    def scan(self, root: Path | None = None) -> CodebaseMap:
        """Scan a Python codebase and build a full map."""
        scan_root = root or self._root
        cmap = CodebaseMap(root=str(scan_root))

        for py_file in self._walk_python_files(scan_root):
            rel_path = str(py_file.relative_to(scan_root))
            try:
                mod_info = self._analyze_module(py_file)
                mod_info.path = rel_path
                cmap.modules[rel_path] = mod_info
                cmap.total_lines += mod_info.lines
                cmap.total_classes += len(mod_info.classes)
                cmap.total_functions += len(mod_info.functions)
                for cls in mod_info.classes:
                    cmap.total_functions += len(cls.methods)
            except Exception as e:
                cmap.modules[rel_path] = ModuleInfo(
                    path=rel_path, errors=[str(e)]
                )

        # Build dependency graph
        cmap.dependency_graph = self._build_dep_graph(cmap)
        return cmap

    def _walk_python_files(self, root: Path):
        """Walk directory tree yielding .py files."""
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune skip dirs
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    yield Path(dirpath) / fn

    def _analyze_module(self, path: Path) -> ModuleInfo:
        """Parse a single Python module with AST."""
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            return ModuleInfo(
                path=str(path),
                lines=source.count("\n") + 1,
                errors=[f"SyntaxError: {e}"],
            )

        mod = ModuleInfo(path=str(path), lines=source.count("\n") + 1)
        mod.docstring = ast.get_docstring(tree) or ""

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                names = [alias.name for alias in node.names]
                mod.from_imports[module_name] = names
            elif isinstance(node, ast.ClassDef):
                mod.classes.append(self._analyze_class(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mod.functions.append(self._analyze_function(node))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        mod.global_vars.append(target.id)

        return mod

    def _analyze_class(self, node: ast.ClassDef) -> ClassInfo:
        """Extract class information."""
        bases = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(ast.unparse(base))

        cls = ClassInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=node.end_lineno or node.lineno,
            bases=bases,
            docstring=ast.get_docstring(node) or "",
            decorators=[ast.unparse(d) for d in node.decorator_list],
        )

        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cls.methods.append(self._analyze_function(child))

        return cls

    def _analyze_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
        """Extract function information."""
        args = []
        for arg in node.args.args:
            args.append(arg.arg)

        return FunctionInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=node.end_lineno or node.lineno,
            args=args,
            decorators=[ast.unparse(d) for d in node.decorator_list],
            docstring=ast.get_docstring(node) or "",
            is_async=isinstance(node, ast.AsyncFunctionDef),
            complexity=self._estimate_complexity(node),
        )

    def _estimate_complexity(self, node: ast.AST) -> int:
        """Estimate McCabe-like cyclomatic complexity."""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For,
                                   ast.ExceptHandler, ast.With,
                                   ast.Assert, ast.comprehension)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def analyze_source(self, source: str, path: str = "<string>") -> ModuleInfo:
        """Analyze a source string without reading from disk."""
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as e:
            return ModuleInfo(
                path=path,
                lines=source.count("\n") + 1,
                errors=[f"SyntaxError: {e}"],
            )

        mod = ModuleInfo(path=path, lines=source.count("\n") + 1)
        mod.docstring = ast.get_docstring(tree) or ""

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                names = [alias.name for alias in node.names]
                mod.from_imports[module_name] = names
            elif isinstance(node, ast.ClassDef):
                mod.classes.append(self._analyze_class(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mod.functions.append(self._analyze_function(node))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        mod.global_vars.append(target.id)

        return mod

    def _build_dep_graph(self, cmap: CodebaseMap) -> dict[str, list[str]]:
        """Build internal dependency graph between modules."""
        graph: dict[str, list[str]] = {}
        module_names = set()

        # Map file paths to importable module names
        for mod_path in cmap.modules:
            parts = mod_path.replace(os.sep, ".").replace("/", ".")
            if parts.endswith(".py"):
                parts = parts[:-3]
            module_names.add(parts)

        for mod_path, mod in cmap.modules.items():
            deps: list[str] = []
            all_imports = list(mod.imports)
            for from_mod in mod.from_imports:
                if from_mod:
                    all_imports.append(from_mod)

            for imp in all_imports:
                # Check if import refers to an internal module
                for internal in module_names:
                    if imp == internal or imp.startswith(internal + ".") or internal.endswith(imp):
                        deps.append(internal)
                        break

            graph[mod_path] = deps

        return graph

    def find_circular_deps(self, cmap: CodebaseMap | None = None) -> list[list[str]]:
        """Find circular dependency chains."""
        if cmap is None:
            cmap = self.scan()

        graph = cmap.dependency_graph
        visited: set[str] = set()
        in_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]):
            if node in in_stack:
                cycle_start = path.index(node)
                cycles.append(path[cycle_start:] + [node])
                return
            if node in visited:
                return
            visited.add(node)
            in_stack.add(node)
            path.append(node)
            for dep in graph.get(node, []):
                dfs(dep, path[:])
            in_stack.discard(node)

        for node in graph:
            if node not in visited:
                dfs(node, [])

        return cycles


def generate_test_skeleton(mod_info: ModuleInfo, import_path: str) -> str:
    """Generate a pytest test skeleton from module metadata.

    Args:
        mod_info: Parsed module info from CodebaseAnalyzer.
        import_path: Dotted Python import path for the module
                     (e.g. ``sharanbot.services.manager``).
    """
    lines: list[str] = [
        f'"""Auto-generated tests for {import_path}."""',
        "",
        "import pytest",
        "",
    ]

    # Collect importable names
    importable: list[str] = []
    for cls in mod_info.classes:
        if not cls.name.startswith("_"):
            importable.append(cls.name)
    for fn in mod_info.functions:
        if not fn.name.startswith("_"):
            importable.append(fn.name)

    if importable:
        names = ", ".join(importable)
        lines.append(f"from {import_path} import {names}")
        lines.append("")

    # Generate test classes for each public class
    for cls in mod_info.classes:
        if cls.name.startswith("_"):
            continue
        lines.append("")
        lines.append(f"class Test{cls.name}:")
        public_methods = [m for m in cls.methods if not m.name.startswith("_")]
        if not public_methods:
            lines.append(f"    def test_{cls.name.lower()}_exists(self):")
            lines.append(f"        assert {cls.name} is not None")
        else:
            for method in public_methods:
                async_prefix = "async " if method.is_async else ""
                lines.append(f"    {async_prefix}def test_{method.name}(self):")
                lines.append(f"        # TODO: test {cls.name}.{method.name}")
                lines.append("        pass")
                lines.append("")

    # Generate test functions for each public top-level function
    for fn in mod_info.functions:
        if fn.name.startswith("_"):
            continue
        lines.append("")
        async_prefix = "async " if fn.is_async else ""
        lines.append(f"{async_prefix}def test_{fn.name}():")
        lines.append(f"    # TODO: test {fn.name}")
        lines.append("    pass")

    lines.append("")
    return "\n".join(lines)
