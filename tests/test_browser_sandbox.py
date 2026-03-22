"""Tests for browser and sandbox tools."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sharanbot.agent.tools.browser import BrowserTool, _validate_browser_url
from sharanbot.agent.tools.sandbox import SandboxTool


# ---------------------------------------------------------------------------
# BrowserTool URL validation
# ---------------------------------------------------------------------------

class TestBrowserURLValidation:
    def test_valid_https_url(self):
        ok, err = _validate_browser_url("https://example.com")
        assert ok is True
        assert err == ""

    def test_valid_http_url(self):
        ok, err = _validate_browser_url("http://localhost:3000")
        assert ok is True

    def test_rejects_file_scheme(self):
        ok, err = _validate_browser_url("file:///etc/passwd")
        assert ok is False
        assert "http" in err.lower() or "allowed" in err.lower()

    def test_rejects_ftp_scheme(self):
        ok, _ = _validate_browser_url("ftp://evil.com/file")
        assert ok is False

    def test_rejects_no_scheme(self):
        ok, _ = _validate_browser_url("example.com")
        assert ok is False

    def test_allows_localhost(self):
        ok, _ = _validate_browser_url("http://localhost:8080")
        assert ok is True


# ---------------------------------------------------------------------------
# BrowserTool basic properties
# ---------------------------------------------------------------------------

class TestBrowserToolProperties:
    def test_name(self):
        tool = BrowserTool()
        assert tool.name == "browser"

    def test_description_mentions_actions(self):
        tool = BrowserTool()
        desc = tool.description
        for action in ("navigate", "screenshot", "click", "type", "get_text"):
            assert action in desc

    def test_parameters_has_action(self):
        tool = BrowserTool()
        props = tool.parameters["properties"]
        assert "action" in props
        assert "navigate" in props["action"]["enum"]

    def test_schema_generation(self):
        tool = BrowserTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "browser"


# ---------------------------------------------------------------------------
# BrowserTool without Playwright installed (graceful error)
# ---------------------------------------------------------------------------

class TestBrowserToolNoPlaywright:
    @pytest.mark.asyncio
    async def test_navigate_without_playwright(self):
        tool = BrowserTool()
        # Mock import failure — execute should catch RuntimeError and return string
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            result = await tool.execute(action="navigate", url="https://example.com")
            assert isinstance(result, str)
            assert "Playwright" in result or "not installed" in result.lower()

    @pytest.mark.asyncio
    async def test_close_without_browser(self):
        tool = BrowserTool()
        result = await tool.execute(action="close")
        assert "closed" in result.lower() or "Browser" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        tool = BrowserTool()
        tool._page = MagicMock()  # Fake page so _ensure_browser doesn't run
        result = await tool.execute(action="fly")
        assert "Unknown" in result or "unknown" in result


# ---------------------------------------------------------------------------
# BrowserTool with mocked Playwright
# ---------------------------------------------------------------------------

class TestBrowserToolMocked:
    @pytest.fixture
    def browser_tool(self, tmp_path):
        tool = BrowserTool(screenshots_dir=tmp_path / "screenshots")
        # Mock page
        mock_page = AsyncMock()
        mock_page.title = AsyncMock(return_value="Test Page")
        mock_page.url = "https://example.com"
        mock_page.inner_text = AsyncMock(return_value="Hello World")
        mock_page.evaluate = AsyncMock(return_value=42)
        mock_page.query_selector = AsyncMock(return_value=AsyncMock(inner_text=AsyncMock(return_value="Element text")))
        mock_page.click = AsyncMock()
        mock_page.fill = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto = AsyncMock(return_value=mock_resp)

        # Create temp screenshot
        ss_dir = tmp_path / "screenshots"
        ss_dir.mkdir(parents=True, exist_ok=True)

        mock_page.screenshot = AsyncMock()

        tool._page = mock_page
        tool._browser = MagicMock()
        tool._context = MagicMock()
        tool._playwright = MagicMock()
        return tool

    @pytest.mark.asyncio
    async def test_navigate(self, browser_tool):
        result = await browser_tool.execute(action="navigate", url="https://example.com")
        assert "Navigated" in result
        assert "200" in result

    @pytest.mark.asyncio
    async def test_navigate_no_url(self, browser_tool):
        result = await browser_tool.execute(action="navigate")
        assert "Error" in result or "required" in result

    @pytest.mark.asyncio
    async def test_click(self, browser_tool):
        result = await browser_tool.execute(action="click", selector="#btn")
        assert "Clicked" in result

    @pytest.mark.asyncio
    async def test_click_no_selector(self, browser_tool):
        result = await browser_tool.execute(action="click")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_type(self, browser_tool):
        result = await browser_tool.execute(action="type", selector="#input", text="hello")
        assert "Typed" in result

    @pytest.mark.asyncio
    async def test_get_text(self, browser_tool):
        result = await browser_tool.execute(action="get_text")
        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_get_text_with_selector(self, browser_tool):
        result = await browser_tool.execute(action="get_text", selector="#content")
        assert "Element text" in result

    @pytest.mark.asyncio
    async def test_evaluate(self, browser_tool):
        result = await browser_tool.execute(action="evaluate", script="1 + 1")
        assert "42" in result

    @pytest.mark.asyncio
    async def test_evaluate_blocks_fetch(self, browser_tool):
        result = await browser_tool.execute(action="evaluate", script="fetch('https://evil.com')")
        assert "disallowed" in result.lower() or "Error" in result

    @pytest.mark.asyncio
    async def test_wait(self, browser_tool):
        result = await browser_tool.execute(action="wait", selector="#loader")
        assert "found" in result.lower()

    @pytest.mark.asyncio
    async def test_scroll(self, browser_tool):
        browser_tool._page.evaluate = AsyncMock(return_value=500)
        result = await browser_tool.execute(action="scroll", direction="down")
        assert "Scrolled" in result

    @pytest.mark.asyncio
    async def test_navigate_invalid_url(self, browser_tool):
        result = await browser_tool.execute(action="navigate", url="ftp://bad.com")
        assert "Invalid" in result or "Error" in result


# ---------------------------------------------------------------------------
# SandboxTool basic properties
# ---------------------------------------------------------------------------

class TestSandboxToolProperties:
    def test_name(self):
        tool = SandboxTool()
        assert tool.name == "sandbox"

    def test_description_mentions_actions(self):
        tool = SandboxTool()
        for action in ("create", "exec", "copy_in", "copy_out", "stop"):
            assert action in tool.description

    def test_parameters_has_action(self):
        tool = SandboxTool()
        props = tool.parameters["properties"]
        assert "action" in props
        assert "create" in props["action"]["enum"]

    def test_schema_generation(self):
        tool = SandboxTool()
        schema = tool.to_schema()
        assert schema["function"]["name"] == "sandbox"


# ---------------------------------------------------------------------------
# SandboxTool list action
# ---------------------------------------------------------------------------

class TestSandboxToolList:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        tool = SandboxTool()
        result = await tool.execute(action="list")
        assert "No active" in result

    @pytest.mark.asyncio
    async def test_list_with_containers(self):
        tool = SandboxTool()
        tool._containers = {"sb-abc123": "sharanbot-sandbox-sb-abc123"}
        result = await tool.execute(action="list")
        assert "sb-abc123" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        tool = SandboxTool()
        result = await tool.execute(action="fly")
        assert "Unknown" in result or "unknown" in result


# ---------------------------------------------------------------------------
# SandboxTool exec without valid sandbox
# ---------------------------------------------------------------------------

class TestSandboxToolExec:
    @pytest.mark.asyncio
    async def test_exec_invalid_sandbox_id(self):
        tool = SandboxTool()
        result = await tool.execute(action="exec", sandbox_id="nonexistent", command="ls")
        assert "Invalid" in result or "none" in result.lower()

    @pytest.mark.asyncio
    async def test_exec_no_command(self):
        tool = SandboxTool()
        tool._containers = {"sb-test": "container-test"}

        # Mock subprocess
        with patch("asyncio.create_subprocess_exec") as mock_proc:
            result = await tool.execute(action="exec", sandbox_id="sb-test")
            assert "Error" in result or "required" in result.lower()


# ---------------------------------------------------------------------------
# SandboxTool create (mocked Docker)
# ---------------------------------------------------------------------------

class TestSandboxToolCreate:
    @pytest.mark.asyncio
    async def test_create_no_docker(self):
        tool = SandboxTool()
        with patch.object(tool, "_docker_available", return_value=False):
            result = await tool.execute(action="create")
            assert "Docker is not available" in result

    @pytest.mark.asyncio
    async def test_create_success(self):
        tool = SandboxTool()
        with patch.object(tool, "_docker_available", return_value=True):
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"abc123container", b""))
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await tool.execute(action="create", image="python:3.11-slim")
                assert "Sandbox created" in result
                assert len(tool._containers) == 1

    @pytest.mark.asyncio
    async def test_create_failure(self):
        tool = SandboxTool()
        with patch.object(tool, "_docker_available", return_value=True):
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"image not found"))
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                result = await tool.execute(action="create")
                assert "Failed" in result


# ---------------------------------------------------------------------------
# SandboxTool stop
# ---------------------------------------------------------------------------

class TestSandboxToolStop:
    @pytest.mark.asyncio
    async def test_stop_removes_container(self):
        tool = SandboxTool()
        tool._containers = {"sb-test": "container-test"}

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await tool.execute(action="stop", sandbox_id="sb-test")
            assert "stopped" in result.lower()
            assert "sb-test" not in tool._containers


# ---------------------------------------------------------------------------
# Integration: tools registered in loop
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_browser_tool_importable(self):
        from sharanbot.agent.tools.browser import BrowserTool
        tool = BrowserTool()
        assert tool.name == "browser"

    def test_sandbox_tool_importable(self):
        from sharanbot.agent.tools.sandbox import SandboxTool
        tool = SandboxTool()
        assert tool.name == "sandbox"


# ---------------------------------------------------------------------------
# Pipeline registry has visual_qa
# ---------------------------------------------------------------------------

class TestVisualQAPipeline:
    def test_visual_qa_exists(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        registry = PipelineRegistry()
        assert "visual_qa" in registry.list_templates()

    def test_visual_qa_has_browser_test_step(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        registry = PipelineRegistry()
        dag = registry.get("visual_qa", "test app")
        task_ids = dag.topological_order()
        assert "visual_test" in task_ids
        node = dag.get_task("visual_test")
        assert node.agent_profile == "e2e_tester"

    def test_full_stack_has_visual_test(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        registry = PipelineRegistry()
        dag = registry.get("full_stack", "test app")
        task_ids = dag.topological_order()
        assert "visual_test" in task_ids

    def test_visual_qa_dag_order(self):
        from sharanbot.agent.team.pipelines import PipelineRegistry
        registry = PipelineRegistry()
        dag = registry.get("visual_qa", "test app")
        visual = dag.get_task("visual_test")
        deploy = dag.get_task("deploy_sandbox")
        impl = dag.get_task("implement")
        # visual_test depends on deploy_sandbox which depends on implement
        assert "deploy_sandbox" in visual.depends_on
        assert "implement" in deploy.depends_on
