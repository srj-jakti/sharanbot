"""TestRunnerTool — structured test execution with parsed results.

Wraps pytest/jest/vitest/flutter test, parses output into structured results
with pass/fail counts, failure details, and coverage info.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


def _detect_test_framework(project_path: Path) -> tuple[str, str]:
    """Detect test framework and return (framework, command).

    Returns ("pytest", "python -m pytest") etc.
    """
    # Python
    if (project_path / "pytest.ini").exists() or (project_path / "conftest.py").exists():
        return "pytest", "python -m pytest"
    pyproject = project_path / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(errors="ignore")
        if "[tool.pytest" in content or "pytest" in content:
            return "pytest", "python -m pytest"

    # Node — check for vitest, jest
    pkg_json = project_path / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text())
            all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            scripts = pkg.get("scripts", {})

            if "vitest" in all_deps or "vitest" in scripts.get("test", ""):
                return "vitest", "npx vitest run"
            if "jest" in all_deps or "jest" in scripts.get("test", ""):
                return "jest", "npx jest"
            if "test" in scripts:
                return "npm-test", "npm test"
        except Exception:
            pass

    # Flutter
    if (project_path / "pubspec.yaml").exists():
        return "flutter", "flutter test"

    # Rust
    if (project_path / "Cargo.toml").exists():
        return "cargo", "cargo test"

    # Go
    if (project_path / "go.mod").exists():
        return "go", "go test ./..."

    # Fallback: check for test directories
    if (project_path / "tests").is_dir() or (project_path / "test").is_dir():
        return "pytest", "python -m pytest"

    return "unknown", ""


class TestRunnerTool(Tool):
    """Run tests with structured result parsing."""

    @property
    def name(self) -> str:
        return "test_runner"

    @property
    def description(self) -> str:
        return (
            "Run project tests with structured results. Auto-detects test framework "
            "(pytest, jest, vitest, flutter test, cargo test, go test). "
            "Returns pass/fail counts, failure details, and coverage info. "
            "Actions: run (execute tests), detect (show detected framework)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "detect"],
                    "description": "Test action: run or detect.",
                },
                "project_path": {
                    "type": "string",
                    "description": "Project directory (defaults to workspace root).",
                },
                "test_path": {
                    "type": "string",
                    "description": "Specific test file or directory to run.",
                },
                "filter": {
                    "type": "string",
                    "description": "Test name filter/pattern (e.g. 'test_login').",
                },
                "coverage": {
                    "type": "boolean",
                    "description": "Enable coverage reporting (default: false).",
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Verbose output (default: false).",
                },
                "framework": {
                    "type": "string",
                    "description": "Override auto-detected framework.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        project_path = kwargs.get("project_path", "")
        root = Path(project_path) if project_path else Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
        if not root.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            root = workspace / root

        fw_override = kwargs.get("framework", "")
        if fw_override:
            framework, base_cmd = fw_override, ""
            # Try to find command
            for fw, cmd in [("pytest", "python -m pytest"), ("jest", "npx jest"),
                            ("vitest", "npx vitest run"), ("flutter", "flutter test"),
                            ("cargo", "cargo test"), ("go", "go test ./...")]:
                if fw == fw_override:
                    base_cmd = cmd
                    break
            if not base_cmd:
                base_cmd = fw_override
        else:
            framework, base_cmd = _detect_test_framework(root)

        if action == "detect":
            return f"Detected test framework: {framework}\nCommand: {base_cmd}\nProject: {root}"

        if framework == "unknown":
            return (
                f"Could not detect test framework in {root}. "
                "No pytest config, package.json with test script, pubspec.yaml, Cargo.toml, or go.mod found."
            )

        # Build command with options
        cmd = self._build_command(framework, base_cmd, **kwargs)

        logger.info("TestRunner: {} → {}", framework, cmd)

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(root),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""

            # Parse results
            parsed = self._parse_results(framework, output, proc.returncode or 0)

            # Truncate raw output if needed
            if len(output) > 8000:
                output = output[:4000] + f"\n\n... ({len(output) - 8000} chars truncated) ...\n\n" + output[-4000:]

            return f"{parsed}\n\n--- Raw Output ---\n{output}"

        except asyncio.TimeoutError:
            return f"❌ Tests timed out after 300s: {cmd}"
        except Exception as e:
            return f"❌ Error running tests: {e}"

    def _build_command(self, framework: str, base_cmd: str, **kwargs: Any) -> str:
        test_path = kwargs.get("test_path", "")
        filter_pattern = kwargs.get("filter", "")
        coverage = kwargs.get("coverage", False)
        verbose = kwargs.get("verbose", False)

        parts = [base_cmd]

        if framework == "pytest":
            if verbose:
                parts.append("-v")
            if coverage:
                parts.append("--cov --cov-report=term-missing")
            if filter_pattern:
                parts.append(f"-k \"{filter_pattern}\"")
            if test_path:
                parts.append(test_path)

        elif framework in ("jest", "vitest"):
            if verbose:
                parts.append("--verbose")
            if coverage:
                parts.append("--coverage")
            if filter_pattern:
                parts.append(f"-t \"{filter_pattern}\"")
            if test_path:
                parts.append(test_path)

        elif framework == "flutter":
            if coverage:
                parts.append("--coverage")
            if test_path:
                parts.append(test_path)

        elif framework == "cargo":
            if test_path:
                parts.append(f"--test {test_path}")
            if filter_pattern:
                parts.append(f"-- {filter_pattern}")

        elif framework == "go":
            if verbose:
                parts.append("-v")
            if coverage:
                parts.append("-cover")
            if test_path:
                parts[0] = f"go test {test_path}"

        return " ".join(parts)

    def _parse_results(self, framework: str, output: str, returncode: int) -> str:
        """Parse test output into structured summary."""
        if framework == "pytest":
            return self._parse_pytest(output, returncode)
        elif framework in ("jest", "vitest"):
            return self._parse_jest(output, returncode)
        elif framework == "flutter":
            return self._parse_flutter(output, returncode)
        else:
            return self._parse_generic(output, returncode)

    def _parse_pytest(self, output: str, returncode: int) -> str:
        """Parse pytest output."""
        # Look for summary line: "X passed, Y failed, Z errors in Ns"
        summary_match = re.search(
            r"=+\s*(.*?)\s*=+\s*$", output, re.MULTILINE,
        )
        summary = summary_match.group(1) if summary_match else "unknown"

        passed = len(re.findall(r"PASSED", output))
        failed = len(re.findall(r"FAILED", output))
        errors = len(re.findall(r"ERROR", output))
        skipped = len(re.findall(r"SKIPPED", output))

        # Also try the summary line numbers
        nums = re.search(r"(\d+) passed", output)
        if nums:
            passed = int(nums.group(1))
        nums = re.search(r"(\d+) failed", output)
        if nums:
            failed = int(nums.group(1))
        nums = re.search(r"(\d+) error", output)
        if nums:
            errors = int(nums.group(1))

        # Extract failure details
        failures = re.findall(r"FAILED (.*?)(?:\n|$)", output)

        status = "✅ ALL PASSED" if returncode == 0 else "❌ FAILURES DETECTED"
        lines = [
            f"Test Results: {status}",
            f"  Passed: {passed}",
            f"  Failed: {failed}",
            f"  Errors: {errors}",
            f"  Skipped: {skipped}",
            f"  Summary: {summary}",
        ]
        if failures:
            lines.append("  Failed tests:")
            for f in failures[:10]:
                lines.append(f"    - {f.strip()}")

        return "\n".join(lines)

    def _parse_jest(self, output: str, returncode: int) -> str:
        """Parse jest/vitest output."""
        # Jest: "Tests: X passed, Y failed, Z total"
        tests_match = re.search(r"Tests:\s+(.*)", output)
        suites_match = re.search(r"Test Suites:\s+(.*)", output)

        status = "✅ ALL PASSED" if returncode == 0 else "❌ FAILURES DETECTED"
        lines = [f"Test Results: {status}"]
        if suites_match:
            lines.append(f"  Suites: {suites_match.group(1)}")
        if tests_match:
            lines.append(f"  Tests: {tests_match.group(1)}")

        # Extract failed test names
        failures = re.findall(r"✕\s+(.*?)(?:\n|$)", output)
        if not failures:
            failures = re.findall(r"FAIL\s+(.*?)(?:\n|$)", output)
        if failures:
            lines.append("  Failed:")
            for f in failures[:10]:
                lines.append(f"    - {f.strip()}")

        return "\n".join(lines)

    def _parse_flutter(self, output: str, returncode: int) -> str:
        """Parse flutter test output."""
        # "All tests passed!" or "X tests passed, Y failed"
        passed = len(re.findall(r"✓", output))
        failed = len(re.findall(r"✗", output))

        status = "✅ ALL PASSED" if returncode == 0 else "❌ FAILURES DETECTED"
        return f"Test Results: {status}\n  Passed: {passed}\n  Failed: {failed}"

    def _parse_generic(self, output: str, returncode: int) -> str:
        """Generic test output parsing."""
        status = "✅ PASSED" if returncode == 0 else "❌ FAILED"
        return f"Test Results: {status} (exit code: {returncode})"
