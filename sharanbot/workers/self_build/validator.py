"""Test validator with regression detection for self-build safety.

Runs tests before and after modifications, identifies newly-failing tests,
and provides clear pass/fail verdict. Used by the self-build agent loop
to ensure modifications don't break existing functionality.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float = 0.0
    error: str = ""
    output: str = ""


@dataclass
class ValidationReport:
    """Complete test validation report."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0
    newly_failing: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    all_results: list[TestResult] = field(default_factory=list)
    regressions_detected: bool = False
    verdict: str = "UNKNOWN"

    @property
    def pass_rate(self) -> float:
        return self.passed / max(self.total, 1)

    def summary(self) -> str:
        lines = [
            f"Verdict: {self.verdict}",
            f"Tests: {self.passed}/{self.total} passed ({self.pass_rate:.0%})",
            f"Duration: {self.duration:.1f}s",
        ]
        if self.newly_failing:
            lines.append(f"REGRESSIONS ({len(self.newly_failing)}):")
            for t in self.newly_failing[:10]:
                lines.append(f"  - {t}")
        if self.fixed:
            lines.append(f"Fixed ({len(self.fixed)}):")
            for t in self.fixed[:10]:
                lines.append(f"  + {t}")
        return "\n".join(lines)


class TestValidator:
    """Run tests and detect regressions compared to a baseline."""

    def __init__(self, workspace: Path | None = None,
                 test_command: str = "python -m pytest --tb=short -q",
                 timeout: int = 300):
        self._workspace = workspace or Path(".")
        self._test_command = test_command
        self._timeout = timeout
        self._baseline: dict[str, bool] | None = None
        self._baseline_path = self._workspace / ".sharanbot" / "test_baseline.json"

    async def run_tests(self, test_path: str = "tests/",
                        markers: str = "") -> ValidationReport:
        """Run test suite and parse results."""
        cmd = self._test_command
        if test_path:
            cmd += f" {test_path}"
        if markers:
            cmd += f" -m {markers}"

        # Use JSON output if pytest-json-report is available
        json_path = self._workspace / ".sharanbot" / "test_results.json"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        cmd += f" --json-report --json-report-file={json_path} 2>/dev/null || {self._test_command}"
        if test_path:
            cmd = f"{self._test_command} {test_path}"

        start = time.time()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout,
            )
            duration = time.time() - start
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            duration = time.time() - start
            return ValidationReport(
                verdict="TIMEOUT",
                duration=duration,
            )
        except Exception as e:
            return ValidationReport(
                verdict=f"ERROR: {e}",
                duration=time.time() - start,
            )

        report = self._parse_pytest_output(output)
        report.duration = duration

        # Detect regressions against baseline
        if self._baseline:
            current_failed = {r.name for r in report.all_results if not r.passed}
            baseline_failed = {name for name, passed in self._baseline.items() if not passed}
            report.newly_failing = sorted(current_failed - baseline_failed)
            report.fixed = sorted(baseline_failed - current_failed)
            report.regressions_detected = len(report.newly_failing) > 0

        # Set verdict
        if report.regressions_detected:
            report.verdict = "REGRESSION"
        elif report.failed == 0 and report.errors == 0:
            report.verdict = "PASS"
        elif report.pass_rate >= 0.95:
            report.verdict = "PASS_WITH_KNOWN_FAILURES"
        else:
            report.verdict = "FAIL"

        return report

    async def capture_baseline(self, test_path: str = "tests/") -> ValidationReport:
        """Run tests and save results as baseline for regression detection."""
        report = await self.run_tests(test_path)

        self._baseline = {
            r.name: r.passed for r in report.all_results
        }
        self._save_baseline()
        logger.info("Captured test baseline: {}/{} passing", report.passed, report.total)
        return report

    def load_baseline(self) -> bool:
        """Load a previously saved baseline."""
        if not self._baseline_path.exists():
            return False
        try:
            data = json.loads(self._baseline_path.read_text(encoding="utf-8"))
            self._baseline = data.get("results", {})
            logger.debug("Loaded test baseline: {} tests", len(self._baseline))
            return True
        except (json.JSONDecodeError, KeyError):
            return False

    def _save_baseline(self):
        self._baseline_path.parent.mkdir(parents=True, exist_ok=True)
        self._baseline_path.write_text(
            json.dumps({"results": self._baseline, "timestamp": time.time()}, indent=2),
            encoding="utf-8",
        )

    def _parse_pytest_output(self, output: str) -> ValidationReport:
        """Parse pytest output into structured report."""
        report = ValidationReport()
        lines = output.splitlines()

        for line in lines:
            stripped = line.strip()
            # Parse test results: PASSED / FAILED / ERROR
            if " PASSED" in stripped or " FAILED" in stripped or " ERROR" in stripped:
                parts = stripped.split()
                if len(parts) >= 2:
                    name = parts[0]
                    status = parts[-1]
                    passed = status == "PASSED"
                    error_msg = ""
                    if not passed:
                        error_msg = stripped
                    report.all_results.append(TestResult(
                        name=name,
                        passed=passed,
                        error=error_msg,
                    ))

            # Parse summary line: "X passed, Y failed, Z errors"
            if "passed" in stripped and ("failed" in stripped or "error" in stripped
                                         or "warning" in stripped or stripped.endswith("passed")):
                self._parse_summary_line(stripped, report)

        report.total = len(report.all_results) if report.all_results else report.total
        report.passed = sum(1 for r in report.all_results if r.passed) if report.all_results else report.passed
        report.failed = report.total - report.passed

        return report

    def _parse_summary_line(self, line: str, report: ValidationReport):
        """Parse pytest summary like '730 passed, 2 failed in 45.23s'."""
        import re
        # Match numbers followed by status words
        for match in re.finditer(r"(\d+)\s+(passed|failed|error|skipped|warning)", line):
            count = int(match.group(1))
            status = match.group(2)
            if status == "passed":
                report.passed = count
            elif status == "failed":
                report.failed = count
            elif status == "error":
                report.errors = count
            elif status == "skipped":
                report.skipped = count
        report.total = report.passed + report.failed + report.errors

        # Parse duration
        duration_match = re.search(r"in\s+([\d.]+)s", line)
        if duration_match:
            report.duration = float(duration_match.group(1))


async def quick_validate(workspace: Path, test_path: str = "tests/") -> bool:
    """Quick validation — returns True if tests pass without regressions."""
    validator = TestValidator(workspace)
    validator.load_baseline()
    report = await validator.run_tests(test_path)
    if report.regressions_detected:
        logger.warning("Regressions detected:\n{}", report.summary())
        return False
    return report.verdict in ("PASS", "PASS_WITH_KNOWN_FAILURES")
