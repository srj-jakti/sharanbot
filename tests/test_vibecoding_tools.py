"""Tests for vibe-coding tools: TemplateEngine, Git, LivePreview, PackageManager, TestRunner, Deploy."""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ── TemplateEngineTool ────────────────────────────────────────────────

class TestTemplateEngineTool:
    """Tests for the TemplateEngineTool (scaffold_project)."""

    def test_importable(self):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        assert tool.name == "scaffold_project"

    def test_description_mentions_frameworks(self):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        desc = tool.description
        for fw in ("nextjs", "vite", "flutter", "expo", "django"):
            assert fw.lower() in desc.lower() or fw in desc

    def test_parameters_has_template(self):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        props = tool.parameters["properties"]
        assert "template" in props
        assert "project_path" in props

    def test_schema_generation(self):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "scaffold_project"

    @pytest.mark.asyncio
    async def test_missing_template(self):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        result = await tool.execute(template="", project_path="/tmp/test")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unknown_template(self):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        result = await tool.execute(template="cobol", project_path="/tmp/test")
        assert "Unknown template" in result

    @pytest.mark.asyncio
    async def test_nextjs_fallback_template(self, tmp_path):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        project_dir = tmp_path / "myapp"
        with patch.dict("os.environ", {"SHARANBOT_WORKSPACE": str(tmp_path)}):
            # Force fallback by making CLI check fail
            with patch("asyncio.create_subprocess_shell", side_effect=FileNotFoundError):
                result = await tool.execute(
                    template="nextjs",
                    project_path=str(project_dir),
                    project_name="myapp",
                    project_description="Test app",
                )
        assert "Scaffolded" in result or "fallback" in result.lower()
        assert project_dir.exists()
        assert (project_dir / "package.json").exists()
        assert (project_dir / "src" / "app" / "page.tsx").exists()

    @pytest.mark.asyncio
    async def test_flutter_fallback_template(self, tmp_path):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        project_dir = tmp_path / "flutterapp"
        with patch.dict("os.environ", {"SHARANBOT_WORKSPACE": str(tmp_path)}):
            with patch("asyncio.create_subprocess_shell", side_effect=FileNotFoundError):
                result = await tool.execute(
                    template="flutter",
                    project_path=str(project_dir),
                    project_name="flutterapp",
                )
        assert project_dir.exists()
        assert (project_dir / "pubspec.yaml").exists()
        assert (project_dir / "lib" / "main.dart").exists()

    @pytest.mark.asyncio
    async def test_expo_fallback_template(self, tmp_path):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        project_dir = tmp_path / "expoapp"
        with patch.dict("os.environ", {"SHARANBOT_WORKSPACE": str(tmp_path)}):
            with patch("asyncio.create_subprocess_shell", side_effect=FileNotFoundError):
                result = await tool.execute(
                    template="expo",
                    project_path=str(project_dir),
                    project_name="expoapp",
                )
        assert project_dir.exists()
        assert (project_dir / "package.json").exists()
        assert (project_dir / "app" / "index.tsx").exists()

    @pytest.mark.asyncio
    async def test_django_fallback_template(self, tmp_path):
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        project_dir = tmp_path / "djangoapp"
        with patch.dict("os.environ", {"SHARANBOT_WORKSPACE": str(tmp_path)}):
            with patch("asyncio.create_subprocess_shell", side_effect=FileNotFoundError):
                result = await tool.execute(
                    template="django",
                    project_path=str(project_dir),
                    project_name="djangoapp",
                )
        assert project_dir.exists()
        assert (project_dir / "manage.py").exists()

    @pytest.mark.asyncio
    async def test_alias_resolution(self, tmp_path):
        """Aliases like 'next', 'react', 'rn' should resolve correctly."""
        from sharanbot.agent.tools.template_engine import _TEMPLATE_ALIASES
        assert _TEMPLATE_ALIASES["next"] == "nextjs"
        assert _TEMPLATE_ALIASES["react"] == "vite-react"
        assert _TEMPLATE_ALIASES["rn"] == "expo"
        assert _TEMPLATE_ALIASES["react-native"] == "expo"

    @pytest.mark.asyncio
    async def test_legacy_type_delegates(self, tmp_path):
        """Legacy types (python, fastapi) should delegate to CreateSoftwareTool."""
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        tool = TemplateEngineTool()
        project_dir = tmp_path / "pyapp"
        with patch.dict("os.environ", {"SHARANBOT_WORKSPACE": str(tmp_path)}):
            result = await tool.execute(
                template="python",
                project_path=str(project_dir),
                project_name="pyapp",
            )
        assert "Created" in result or "pyapp" in result


# ── GitTool ───────────────────────────────────────────────────────────

class TestGitTool:
    """Tests for the GitTool."""

    def test_importable(self):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool()
        assert tool.name == "git"

    def test_all_actions_listed(self):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool()
        actions = tool.parameters["properties"]["action"]["enum"]
        for a in ("init", "status", "diff", "add", "commit", "branch",
                   "checkout", "log", "push", "pull", "stash", "tag"):
            assert a in actions

    @pytest.mark.asyncio
    async def test_init_and_status(self, tmp_path):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool(workspace=tmp_path)
        result = await tool.execute(action="init", working_dir=str(tmp_path))
        assert "Initialized" in result or "init" in result.lower()

        result = await tool.execute(action="status", working_dir=str(tmp_path))
        assert "clean" in result.lower() or "status" in result.lower() or "##" in result

    @pytest.mark.asyncio
    async def test_add_and_commit(self, tmp_path):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool(workspace=tmp_path)
        await tool.execute(action="init", working_dir=str(tmp_path))

        # Create a file
        (tmp_path / "test.txt").write_text("hello")

        result = await tool.execute(action="add", files=["."], working_dir=str(tmp_path))
        assert "Staged" in result

        result = await tool.execute(action="commit", message="initial commit", working_dir=str(tmp_path))
        assert "Committed" in result or "commit" in result.lower()

    @pytest.mark.asyncio
    async def test_commit_requires_message(self, tmp_path):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool(workspace=tmp_path)
        result = await tool.execute(action="commit", working_dir=str(tmp_path))
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_branch_list(self, tmp_path):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool(workspace=tmp_path)
        await tool.execute(action="init", working_dir=str(tmp_path))
        (tmp_path / "f.txt").write_text("x")
        await tool.execute(action="add", files=["."], working_dir=str(tmp_path))
        await tool.execute(action="commit", message="init", working_dir=str(tmp_path))

        result = await tool.execute(action="branch", working_dir=str(tmp_path))
        assert "main" in result or "master" in result or "Branches" in result

    @pytest.mark.asyncio
    async def test_log(self, tmp_path):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool(workspace=tmp_path)
        await tool.execute(action="init", working_dir=str(tmp_path))
        (tmp_path / "f.txt").write_text("x")
        await tool.execute(action="add", files=["."], working_dir=str(tmp_path))
        await tool.execute(action="commit", message="test commit", working_dir=str(tmp_path))

        result = await tool.execute(action="log", working_dir=str(tmp_path))
        assert "test commit" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        from sharanbot.agent.tools.git import GitTool
        tool = GitTool()
        result = await tool.execute(action="fly")
        assert "Unknown" in result


# ── PackageManagerTool ────────────────────────────────────────────────

class TestPackageManagerTool:
    """Tests for the PackageManagerTool."""

    def test_importable(self):
        from sharanbot.agent.tools.package_manager import PackageManagerTool
        tool = PackageManagerTool()
        assert tool.name == "package_manager"

    def test_detect_npm(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "package.json").write_text("{}")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "npm"
        assert eco == "node"

    def test_detect_yarn(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "yarn.lock").write_text("")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "yarn"

    def test_detect_pnpm(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "pnpm"

    def test_detect_pip(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "requirements.txt").write_text("flask")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "pip"
        assert eco == "python"

    def test_detect_poetry(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "poetry"

    def test_detect_flutter(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "pubspec.yaml").write_text("name: test")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "flutter"
        assert eco == "dart"

    def test_detect_cargo(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "cargo"
        assert eco == "rust"

    def test_detect_go(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        (tmp_path / "go.mod").write_text("module test")
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "go"
        assert eco == "go"

    def test_detect_unknown(self, tmp_path):
        from sharanbot.agent.tools.package_manager import detect_package_manager
        mgr, eco = detect_package_manager(tmp_path)
        assert mgr == "unknown"

    @pytest.mark.asyncio
    async def test_detect_action(self, tmp_path):
        from sharanbot.agent.tools.package_manager import PackageManagerTool
        tool = PackageManagerTool()
        (tmp_path / "package.json").write_text("{}")
        result = await tool.execute(action="detect", project_path=str(tmp_path))
        assert "npm" in result

    @pytest.mark.asyncio
    async def test_add_requires_packages(self, tmp_path):
        from sharanbot.agent.tools.package_manager import PackageManagerTool
        tool = PackageManagerTool()
        (tmp_path / "package.json").write_text("{}")
        result = await tool.execute(action="add", project_path=str(tmp_path))
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unknown_manager(self, tmp_path):
        from sharanbot.agent.tools.package_manager import PackageManagerTool
        tool = PackageManagerTool()
        result = await tool.execute(action="install", project_path=str(tmp_path))
        assert "Could not detect" in result


# ── TestRunnerTool ────────────────────────────────────────────────────

class TestTestRunnerTool:
    """Tests for the TestRunnerTool."""

    def test_importable(self):
        from sharanbot.agent.tools.test_runner import TestRunnerTool
        tool = TestRunnerTool()
        assert tool.name == "test_runner"

    def test_detect_pytest(self, tmp_path):
        from sharanbot.agent.tools.test_runner import _detect_test_framework
        (tmp_path / "conftest.py").write_text("")
        fw, cmd = _detect_test_framework(tmp_path)
        assert fw == "pytest"
        assert "pytest" in cmd

    def test_detect_vitest(self, tmp_path):
        from sharanbot.agent.tools.test_runner import _detect_test_framework
        pkg = {"devDependencies": {"vitest": "^1.0"}, "scripts": {"test": "vitest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        fw, cmd = _detect_test_framework(tmp_path)
        assert fw == "vitest"

    def test_detect_jest(self, tmp_path):
        from sharanbot.agent.tools.test_runner import _detect_test_framework
        pkg = {"devDependencies": {"jest": "^29"}, "scripts": {"test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        fw, cmd = _detect_test_framework(tmp_path)
        assert fw == "jest"

    def test_detect_flutter(self, tmp_path):
        from sharanbot.agent.tools.test_runner import _detect_test_framework
        (tmp_path / "pubspec.yaml").write_text("name: test")
        fw, cmd = _detect_test_framework(tmp_path)
        assert fw == "flutter"

    def test_detect_cargo(self, tmp_path):
        from sharanbot.agent.tools.test_runner import _detect_test_framework
        (tmp_path / "Cargo.toml").write_text("[package]")
        fw, cmd = _detect_test_framework(tmp_path)
        assert fw == "cargo"

    @pytest.mark.asyncio
    async def test_detect_action(self, tmp_path):
        from sharanbot.agent.tools.test_runner import TestRunnerTool
        tool = TestRunnerTool()
        (tmp_path / "conftest.py").write_text("")
        result = await tool.execute(action="detect", project_path=str(tmp_path))
        assert "pytest" in result

    def test_parse_pytest_output(self):
        from sharanbot.agent.tools.test_runner import TestRunnerTool
        tool = TestRunnerTool()
        output = "PASSED test_a\nPASSED test_b\nFAILED test_c\n=== 2 passed, 1 failed in 1.5s ==="
        result = tool._parse_pytest(output, 1)
        assert "Passed: 2" in result
        assert "Failed: 1" in result
        assert "FAILURES" in result

    def test_parse_jest_output(self):
        from sharanbot.agent.tools.test_runner import TestRunnerTool
        tool = TestRunnerTool()
        output = "Test Suites: 1 passed, 1 total\nTests: 5 passed, 5 total"
        result = tool._parse_jest(output, 0)
        assert "ALL PASSED" in result
        assert "5 passed" in result


# ── LivePreviewTool ───────────────────────────────────────────────────

class TestLivePreviewTool:
    """Tests for the LivePreviewTool."""

    def test_importable(self):
        from sharanbot.agent.tools.live_preview import LivePreviewTool
        tool = LivePreviewTool()
        assert tool.name == "live_preview"

    def test_detect_nextjs(self, tmp_path):
        from sharanbot.agent.tools.live_preview import detect_framework
        (tmp_path / "next.config.js").write_text("")
        assert detect_framework(tmp_path) == "nextjs"

    def test_detect_vite(self, tmp_path):
        from sharanbot.agent.tools.live_preview import detect_framework
        (tmp_path / "vite.config.ts").write_text("")
        assert detect_framework(tmp_path) == "vite"

    def test_detect_django(self, tmp_path):
        from sharanbot.agent.tools.live_preview import detect_framework
        (tmp_path / "manage.py").write_text("DJANGO_SETTINGS_MODULE")
        assert detect_framework(tmp_path) == "django"

    def test_detect_flutter(self, tmp_path):
        from sharanbot.agent.tools.live_preview import detect_framework
        (tmp_path / "pubspec.yaml").write_text("")
        assert detect_framework(tmp_path) == "flutter"

    def test_detect_none(self, tmp_path):
        from sharanbot.agent.tools.live_preview import detect_framework
        assert detect_framework(tmp_path) is None

    @pytest.mark.asyncio
    async def test_start_requires_path(self):
        from sharanbot.agent.tools.live_preview import LivePreviewTool
        tool = LivePreviewTool()
        result = await tool.execute(action="start")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        from sharanbot.agent.tools.live_preview import LivePreviewTool
        tool = LivePreviewTool()
        result = await tool.execute(action="fly")
        assert "Unknown" in result


# ── DeployTool ────────────────────────────────────────────────────────

class TestDeployTool:
    """Tests for the DeployTool."""

    def test_importable(self):
        from sharanbot.agent.tools.deploy import DeployTool
        tool = DeployTool()
        assert tool.name == "deploy"

    def test_detect_vercel(self, tmp_path):
        from sharanbot.agent.tools.deploy import detect_deploy_target
        (tmp_path / "vercel.json").write_text("{}")
        assert detect_deploy_target(tmp_path) == "vercel"

    def test_detect_netlify(self, tmp_path):
        from sharanbot.agent.tools.deploy import detect_deploy_target
        (tmp_path / "netlify.toml").write_text("")
        assert detect_deploy_target(tmp_path) == "netlify"

    def test_detect_docker(self, tmp_path):
        from sharanbot.agent.tools.deploy import detect_deploy_target
        (tmp_path / "Dockerfile").write_text("FROM node")
        assert detect_deploy_target(tmp_path) == "docker"

    def test_detect_docker_compose(self, tmp_path):
        from sharanbot.agent.tools.deploy import detect_deploy_target
        (tmp_path / "docker-compose.yml").write_text("version: '3'")
        assert detect_deploy_target(tmp_path) == "docker-compose"

    def test_detect_nextjs_as_vercel(self, tmp_path):
        from sharanbot.agent.tools.deploy import detect_deploy_target
        (tmp_path / "next.config.js").write_text("")
        assert detect_deploy_target(tmp_path) == "vercel"

    def test_detect_none(self, tmp_path):
        from sharanbot.agent.tools.deploy import detect_deploy_target
        assert detect_deploy_target(tmp_path) is None

    @pytest.mark.asyncio
    async def test_detect_action(self, tmp_path):
        from sharanbot.agent.tools.deploy import DeployTool
        tool = DeployTool()
        (tmp_path / "vercel.json").write_text("{}")
        result = await tool.execute(action="detect", project_path=str(tmp_path))
        assert "vercel" in result

    @pytest.mark.asyncio
    async def test_deploy_no_target(self, tmp_path):
        from sharanbot.agent.tools.deploy import DeployTool
        tool = DeployTool()
        result = await tool.execute(action="deploy", project_path=str(tmp_path))
        assert "Could not detect" in result


# ── Tool Registration ─────────────────────────────────────────────────

class TestToolRegistration:
    """Verify all new tools are properly registered in AgentLoop."""

    def test_new_tools_importable_from_loop(self):
        """All new tool imports in loop.py should work."""
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        from sharanbot.agent.tools.git import GitTool
        from sharanbot.agent.tools.live_preview import LivePreviewTool
        from sharanbot.agent.tools.package_manager import PackageManagerTool
        from sharanbot.agent.tools.test_runner import TestRunnerTool
        from sharanbot.agent.tools.deploy import DeployTool

        tools = [
            TemplateEngineTool(), GitTool(), LivePreviewTool(),
            PackageManagerTool(), TestRunnerTool(), DeployTool(),
        ]
        names = {t.name for t in tools}
        assert "scaffold_project" in names
        assert "git" in names
        assert "live_preview" in names
        assert "package_manager" in names
        assert "test_runner" in names
        assert "deploy" in names

    def test_all_tools_have_valid_schemas(self):
        """All new tools should produce valid OpenAI function schemas."""
        from sharanbot.agent.tools.template_engine import TemplateEngineTool
        from sharanbot.agent.tools.git import GitTool
        from sharanbot.agent.tools.live_preview import LivePreviewTool
        from sharanbot.agent.tools.package_manager import PackageManagerTool
        from sharanbot.agent.tools.test_runner import TestRunnerTool
        from sharanbot.agent.tools.deploy import DeployTool

        for ToolCls in (TemplateEngineTool, GitTool, LivePreviewTool,
                        PackageManagerTool, TestRunnerTool, DeployTool):
            tool = ToolCls() if ToolCls != GitTool else GitTool()
            schema = tool.to_schema()
            assert schema["type"] == "function"
            assert "name" in schema["function"]
            assert "description" in schema["function"]
            assert "parameters" in schema["function"]


# ── FormatTool ────────────────────────────────────────────────────────

class TestFormatTool:
    """Tests for the FormatTool (format_code)."""

    def test_importable(self):
        from sharanbot.agent.tools.format_code import FormatTool
        tool = FormatTool()
        assert tool.name == "format_code"

    def test_description(self):
        from sharanbot.agent.tools.format_code import FormatTool
        tool = FormatTool()
        assert "format" in tool.description.lower()
        assert "lint" in tool.description.lower()

    def test_parameters_has_action(self):
        from sharanbot.agent.tools.format_code import FormatTool
        tool = FormatTool()
        props = tool.parameters["properties"]
        assert "action" in props
        assert set(props["action"]["enum"]) == {"format", "lint", "detect", "check"}

    def test_detect_ruff(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 120\n")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "ruff" in names

    def test_detect_black(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 88\n")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "black" in names

    def test_detect_prettier(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / ".prettierrc").write_text("{}")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "prettier" in names

    def test_detect_eslint(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "eslint.config.js").write_text("module.exports = {}")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "eslint" in names

    def test_detect_rustfmt(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "rustfmt" in names
        assert "clippy" in names

    def test_detect_gofmt(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "go.mod").write_text("module test")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "gofmt" in names

    def test_detect_dart(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "pubspec.yaml").write_text("name: test")
        fmts = detect_formatters(tmp_path)
        names = [f["name"] for f in fmts]
        assert "dart format" in names

    def test_detect_none(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        fmts = detect_formatters(tmp_path)
        assert len(fmts) == 0

    def test_detect_fallback_python(self, tmp_path):
        from sharanbot.agent.tools.format_code import detect_formatters
        (tmp_path / "main.py").write_text("print('hello')")
        fmts = detect_formatters(tmp_path)
        assert len(fmts) >= 1
        assert fmts[0]["name"] == "ruff"

    @pytest.mark.asyncio
    async def test_detect_action(self, tmp_path):
        from sharanbot.agent.tools.format_code import FormatTool
        tool = FormatTool()
        (tmp_path / ".prettierrc").write_text("{}")
        result = await tool.execute(action="detect", project_path=str(tmp_path))
        assert "prettier" in result

    @pytest.mark.asyncio
    async def test_no_formatters(self, tmp_path):
        from sharanbot.agent.tools.format_code import FormatTool
        tool = FormatTool()
        result = await tool.execute(action="format", project_path=str(tmp_path))
        assert "No formatters" in result

    def test_schema_generation(self):
        from sharanbot.agent.tools.format_code import FormatTool
        tool = FormatTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "format_code"


# ── DesignToCodeTool ──────────────────────────────────────────────────

class TestDesignToCodeTool:
    """Tests for the DesignToCodeTool (design_to_code)."""

    def test_importable(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        assert tool.name == "design_to_code"

    def test_description(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        assert "design" in tool.description.lower()
        assert "code" in tool.description.lower()

    def test_parameters_has_action(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        props = tool.parameters["properties"]
        assert "action" in props
        assert "image_path" in props
        assert "framework" in props
        assert "style" in props

    def test_frameworks_listed(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        fws = tool.parameters["properties"]["framework"]["enum"]
        for fw in ("react", "nextjs", "vue", "flutter", "html"):
            assert fw in fws

    @pytest.mark.asyncio
    async def test_no_provider_error(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        result = await tool.execute(action="generate", design_description="A login page")
        assert "Error" in result
        assert "provider" in result.lower()

    @pytest.mark.asyncio
    async def test_no_input_error(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        mock_provider = MagicMock()
        tool = DesignToCodeTool(provider=mock_provider, model="test")
        result = await tool.execute(action="generate")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_generate_with_description(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "```tsx\nexport default function LoginPage() { return <div>Login</div> }\n```"
        mock_provider.chat_with_retry = AsyncMock(return_value=mock_response)
        tool = DesignToCodeTool(provider=mock_provider, model="test")
        result = await tool.execute(action="generate", design_description="A login page with email and password fields")
        assert "LoginPage" in result or "Login" in result

    @pytest.mark.asyncio
    async def test_generate_writes_file(self, tmp_path):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "```tsx\nexport default function Card() { return <div>Card</div> }\n```"
        mock_provider.chat_with_retry = AsyncMock(return_value=mock_response)
        tool = DesignToCodeTool(provider=mock_provider, model="test")
        out_file = tmp_path / "Card.tsx"
        result = await tool.execute(
            action="generate",
            design_description="A card component",
            output_path=str(out_file),
        )
        assert out_file.exists()
        assert "Card" in out_file.read_text()

    @pytest.mark.asyncio
    async def test_describe_action(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "This design shows a dashboard with a sidebar and main content area."
        mock_provider.chat_with_retry = AsyncMock(return_value=mock_response)
        tool = DesignToCodeTool(provider=mock_provider, model="test")
        result = await tool.execute(action="describe", design_description="A dashboard layout")
        assert "dashboard" in result.lower()

    def test_set_provider(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        assert tool._provider is None
        mock_provider = MagicMock()
        tool.set_provider(mock_provider, "gpt-4o")
        assert tool._provider is mock_provider
        assert tool._model == "gpt-4o"

    def test_extract_code(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        code = DesignToCodeTool._extract_code("```tsx\nconst x = 1;\n```")
        assert code == "const x = 1;"
        raw = DesignToCodeTool._extract_code("just plain text")
        assert raw == "just plain text"

    def test_load_image_nonexistent(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        result = DesignToCodeTool._load_image("/nonexistent/image.png")
        assert result is None

    def test_load_image_real(self, tmp_path):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with patch.dict("os.environ", {"SHARANBOT_WORKSPACE": str(tmp_path)}):
            result = DesignToCodeTool._load_image(str(img))
        assert result is not None
        assert result.startswith("data:image/png;base64,")

    def test_schema_generation(self):
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        tool = DesignToCodeTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "design_to_code"


# ── DatabaseTool ──────────────────────────────────────────────────────

class TestDatabaseTool:
    """Tests for the DatabaseTool (database)."""

    def test_importable(self):
        from sharanbot.agent.tools.database import DatabaseTool
        tool = DatabaseTool()
        assert tool.name == "database"

    def test_description(self):
        from sharanbot.agent.tools.database import DatabaseTool
        tool = DatabaseTool()
        assert "database" in tool.description.lower() or "migrate" in tool.description.lower()

    def test_parameters_has_action(self):
        from sharanbot.agent.tools.database import DatabaseTool
        tool = DatabaseTool()
        props = tool.parameters["properties"]
        assert "action" in props
        actions = props["action"]["enum"]
        for a in ("migrate", "deploy", "seed", "reset", "status", "push", "pull", "generate", "studio"):
            assert a in actions

    def test_detect_prisma(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "prisma").mkdir()
        (tmp_path / "prisma" / "schema.prisma").write_text("generator client {}")
        orm, config = detect_database_tool(tmp_path)
        assert orm == "prisma"

    def test_detect_drizzle(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "drizzle.config.ts").write_text("export default {}")
        orm, config = detect_database_tool(tmp_path)
        assert orm == "drizzle"

    def test_detect_django(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "manage.py").write_text("DJANGO_SETTINGS_MODULE")
        orm, config = detect_database_tool(tmp_path)
        assert orm == "django"

    def test_detect_alembic(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "alembic.ini").write_text("[alembic]")
        orm, config = detect_database_tool(tmp_path)
        assert orm == "alembic"

    def test_detect_typeorm(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "ormconfig.json").write_text("{}")
        orm, config = detect_database_tool(tmp_path)
        assert orm == "typeorm"

    def test_detect_knex(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "package.json").write_text('{"dependencies": {"knex": "^3.0"}}')
        orm, config = detect_database_tool(tmp_path)
        assert orm == "knex"

    def test_detect_diesel(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "Cargo.toml").write_text('[dependencies]\ndiesel = "2.0"')
        orm, config = detect_database_tool(tmp_path)
        assert orm == "diesel"

    def test_detect_goose(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        (tmp_path / "go.mod").write_text("module test\nrequire github.com/pressly/goose v3")
        orm, config = detect_database_tool(tmp_path)
        assert orm == "goose"

    def test_detect_none(self, tmp_path):
        from sharanbot.agent.tools.database import detect_database_tool
        orm, config = detect_database_tool(tmp_path)
        assert orm == "unknown"

    @pytest.mark.asyncio
    async def test_status_action_prisma(self, tmp_path):
        from sharanbot.agent.tools.database import DatabaseTool
        tool = DatabaseTool()
        (tmp_path / "prisma").mkdir()
        (tmp_path / "prisma" / "schema.prisma").write_text("generator client {}")
        with patch("asyncio.create_subprocess_shell") as mock_proc:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"Status output", b""))
            proc.returncode = 0
            mock_proc.return_value = proc
            result = await tool.execute(action="status", project_path=str(tmp_path))
        assert "prisma" in result.lower() or "Status" in result or "status" in result.lower()

    @pytest.mark.asyncio
    async def test_no_orm_detected(self, tmp_path):
        from sharanbot.agent.tools.database import DatabaseTool
        tool = DatabaseTool()
        result = await tool.execute(action="status", project_path=str(tmp_path))
        assert "No database" in result or "Could not detect" in result or "unknown" in result.lower()

    def test_schema_generation(self):
        from sharanbot.agent.tools.database import DatabaseTool
        tool = DatabaseTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "database"


# ── New Tool Registration ─────────────────────────────────────────────

class TestNewToolRegistration:
    """Verify the 3 new tools are importable and have valid schemas."""

    def test_all_three_importable(self):
        from sharanbot.agent.tools.format_code import FormatTool
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        from sharanbot.agent.tools.database import DatabaseTool

        tools = [FormatTool(), DesignToCodeTool(), DatabaseTool()]
        names = {t.name for t in tools}
        assert "format_code" in names
        assert "design_to_code" in names
        assert "database" in names

    def test_all_three_valid_schemas(self):
        from sharanbot.agent.tools.format_code import FormatTool
        from sharanbot.agent.tools.design_to_code import DesignToCodeTool
        from sharanbot.agent.tools.database import DatabaseTool

        for ToolCls in (FormatTool, DesignToCodeTool, DatabaseTool):
            tool = ToolCls()
            schema = tool.to_schema()
            assert schema["type"] == "function"
            assert "name" in schema["function"]
            assert "parameters" in schema["function"]
