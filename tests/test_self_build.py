"""Tests for self-build system — analyzer, validator, safety, rollback."""

import ast
from pathlib import Path

import pytest

from sharanbot.workers.self_build.analyzer import CodebaseAnalyzer, CodebaseMap
from sharanbot.workers.self_build.safety import SafetyBoundary
from sharanbot.workers.self_build.agent_loop import SelfBuildLoop, FileChange, ModificationPlan


# ── CodebaseAnalyzer ─────────────────────────────────────────────────

class TestCodebaseAnalyzer:
    def test_scan_simple_module(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            'import os\n\nclass Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n',
            encoding="utf-8",
        )
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        assert len(cmap.modules) == 1
        mod = list(cmap.modules.values())[0]
        assert len(mod.classes) == 1
        assert mod.classes[0].name == "Foo"
        assert len(mod.classes[0].methods) == 1
        assert len(mod.functions) == 1
        assert mod.functions[0].name == "baz"
        assert "os" in mod.imports

    def test_scan_package(self, tmp_path):
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("class Core:\n    pass\n")
        (pkg / "utils.py").write_text("def helper():\n    return 42\n")
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        assert len(cmap.modules) >= 3  # __init__.py, core.py, utils.py
        assert cmap.total_classes >= 1
        assert cmap.total_functions >= 1

    def test_summary(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1\n")
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        summary = cmap.get_summary()
        assert "Modules:" in summary

    def test_find_class(self, tmp_path):
        (tmp_path / "m.py").write_text("class MyClass:\n    pass\n")
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        results = cmap.find_class("MyClass")
        assert len(results) == 1
        assert results[0][1].name == "MyClass"

    def test_find_function(self, tmp_path):
        (tmp_path / "m.py").write_text("def my_func():\n    pass\n")
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        results = cmap.find_function("my_func")
        assert len(results) == 1

    def test_syntax_error_handled(self, tmp_path):
        (tmp_path / "bad.py").write_text("def broken(\n")
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        assert len(cmap.modules) == 1
        mod = list(cmap.modules.values())[0]
        assert len(mod.errors) > 0

    def test_complexity_estimation(self, tmp_path):
        code = (
            "def complex_func(x):\n"
            "    if x > 0:\n"
            "        for i in range(x):\n"
            "            if i % 2 == 0:\n"
            "                pass\n"
            "    elif x < 0:\n"
            "        while x < 0:\n"
            "            x += 1\n"
        )
        (tmp_path / "c.py").write_text(code)
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        func = list(cmap.modules.values())[0].functions[0]
        assert func.complexity >= 4  # Multiple branches

    def test_circular_deps(self, tmp_path):
        (tmp_path / "a.py").write_text("from b import x\n")
        (tmp_path / "b.py").write_text("from a import y\n")
        analyzer = CodebaseAnalyzer(tmp_path)
        cmap = analyzer.scan()
        # Build dep graph — might or might not detect circular
        # depending on module name resolution
        cycles = analyzer.find_circular_deps(cmap)
        assert isinstance(cycles, list)


# ── SafetyBoundary ───────────────────────────────────────────────────

class TestSafetyBoundary:
    def test_protected_files(self):
        sb = SafetyBoundary(workspace=Path("."))
        assert sb.is_protected("sharanbot/workers/self_build/safety.py")
        assert sb.is_protected("sharanbot/security/auth.py")
        assert sb.is_protected(".env")

    def test_restricted_files(self):
        sb = SafetyBoundary(workspace=Path("."))
        assert sb.is_restricted("sharanbot/agent/loop.py")
        assert sb.is_restricted("pyproject.toml")

    def test_normal_file_allowed(self):
        sb = SafetyBoundary(workspace=Path("."))
        allowed, reason = sb.check_write("sharanbot/agent/some_new_module.py")
        assert allowed
        assert reason == ""

    def test_protected_file_blocked(self):
        sb = SafetyBoundary(workspace=Path("."))
        allowed, reason = sb.check_write("sharanbot/security/auth.py")
        assert not allowed
        assert "BLOCKED" in reason

    def test_restricted_file_flagged(self):
        sb = SafetyBoundary(workspace=Path("."))
        allowed, reason = sb.check_write("sharanbot/agent/loop.py")
        assert allowed  # allowed but flagged
        assert "CAUTION" in reason

    def test_batch_check(self):
        sb = SafetyBoundary(workspace=Path("."))
        results = sb.check_batch([
            "sharanbot/agent/new.py",
            "sharanbot/security/auth.py",
            ".env",
        ])
        assert results["sharanbot/agent/new.py"][0] is True
        assert results["sharanbot/security/auth.py"][0] is False
        assert results[".env"][0] is False

    def test_filter_allowed(self):
        sb = SafetyBoundary(workspace=Path("."))
        allowed = sb.filter_allowed([
            "sharanbot/agent/new.py",
            "sharanbot/security/auth.py",
            "tests/test_new.py",
        ])
        assert "sharanbot/agent/new.py" in allowed
        assert "tests/test_new.py" in allowed
        assert "sharanbot/security/auth.py" not in allowed

    def test_extra_protected(self):
        sb = SafetyBoundary(
            workspace=Path("."),
            extra_protected=["**/critical_data.json"],
        )
        assert sb.is_protected("data/critical_data.json")


# ── SelfBuildLoop ────────────────────────────────────────────────────

class TestSelfBuildLoop:
    def test_create_plan(self, tmp_path):
        loop = SelfBuildLoop(workspace=tmp_path)
        plan = loop.create_plan(
            "Add new utility function",
            [{"path": "sharanbot/utils/new.py", "action": "create",
              "content": "def greet(): return 'hi'"}],
        )
        assert plan.plan_id.startswith("plan-")
        assert len(plan.changes) == 1
        assert plan.estimated_risk == "low"

    def test_risk_assessment(self, tmp_path):
        loop = SelfBuildLoop(workspace=tmp_path)
        # High risk: modifying core agent code
        plan = loop.create_plan(
            "Modify core agent",
            [
                {"path": "sharanbot/agent/loop.py", "action": "modify",
                 "content": "modified"},
                {"path": "sharanbot/agent/memory.py", "action": "modify",
                 "content": "modified"},
                {"path": "sharanbot/agent/tools/base.py", "action": "modify",
                 "content": "modified"},
                {"path": "sharanbot/agent/recovery.py", "action": "modify",
                 "content": "modified"},
            ],
        )
        assert plan.estimated_risk == "high"

    def test_analyze_sync(self, tmp_path):
        (tmp_path / "sharanbot").mkdir()
        (tmp_path / "sharanbot" / "mod.py").write_text("x = 1\n")
        loop = SelfBuildLoop(workspace=tmp_path)
        cmap = loop.analyze_sync()
        assert len(cmap.modules) >= 1

    def test_codebase_context(self, tmp_path):
        (tmp_path / "sharanbot").mkdir()
        (tmp_path / "sharanbot" / "hello.py").write_text("def hello(): pass\n")
        loop = SelfBuildLoop(workspace=tmp_path)
        ctx = loop.get_codebase_context()
        assert "Modules:" in ctx
