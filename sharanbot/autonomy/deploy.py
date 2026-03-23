"""Deployment pipeline — auto-detect, build, containerize, health-check, rollback."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class DeployStage(str, Enum):
    DETECT = "detect"
    VALIDATE = "validate"
    BUILD = "build"
    CONTAINERIZE = "containerize"
    DEPLOY = "deploy"
    HEALTH_CHECK = "health_check"
    DONE = "done"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ProjectType(str, Enum):
    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    GO = "go"
    JAVA = "java"
    STATIC = "static"
    UNKNOWN = "unknown"


@dataclass
class DeployConfig:
    project_path: Path = field(default_factory=lambda: Path("."))
    deploy_id: str = ""
    port: int = 8000
    health_endpoint: str = "/health"
    health_timeout: int = 30
    max_retries: int = 2
    env_vars: dict[str, str] = field(default_factory=dict)
    docker_registry: str = ""
    compose_override: str = ""

    def __post_init__(self):
        if not self.deploy_id:
            self.deploy_id = f"deploy-{uuid.uuid4().hex[:8]}"


@dataclass
class DeployResult:
    deploy_id: str
    stage: DeployStage
    success: bool
    project_type: str = ""
    container_id: str = ""
    error: str = ""
    logs: list[str] = field(default_factory=list)


# ── Project type detection indicators ─────────────────────────────────

_DETECT_MAP: dict[str, ProjectType] = {
    "pyproject.toml": ProjectType.PYTHON,
    "setup.py": ProjectType.PYTHON,
    "requirements.txt": ProjectType.PYTHON,
    "Pipfile": ProjectType.PYTHON,
    "package.json": ProjectType.NODE,
    "Cargo.toml": ProjectType.RUST,
    "go.mod": ProjectType.GO,
    "pom.xml": ProjectType.JAVA,
    "build.gradle": ProjectType.JAVA,
    "index.html": ProjectType.STATIC,
}


# ── Dockerfile templates ─────────────────────────────────────────────

_DOCKERFILE_TEMPLATES: dict[ProjectType, str] = {
    ProjectType.PYTHON: """\
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt* pyproject.toml* ./
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || pip install --no-cache-dir . 2>/dev/null || true
COPY . .
EXPOSE {port}
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
""",
    ProjectType.NODE: """\
FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY . .
EXPOSE {port}
CMD ["node", "index.js"]
""",
    ProjectType.RUST: """\
FROM rust:1.80-slim AS builder
WORKDIR /app
COPY . .
RUN cargo build --release
FROM debian:bookworm-slim
COPY --from=builder /app/target/release/app /usr/local/bin/app
EXPOSE {port}
CMD ["app"]
""",
    ProjectType.GO: """\
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o server .
FROM alpine:3.19
COPY --from=builder /app/server /server
EXPOSE {port}
CMD ["/server"]
""",
    ProjectType.STATIC: """\
FROM nginx:alpine
COPY . /usr/share/nginx/html
EXPOSE 80
""",
}


class DeploymentPipeline:
    """Full deployment pipeline with auto-detection, build, and health checks."""

    def __init__(self, config: DeployConfig | None = None):
        self.config = config or DeployConfig()
        self._stage = DeployStage.DETECT
        self._project_type = ProjectType.UNKNOWN
        self._logs: list[str] = []

    def detect_project_type(self, project_path: Path | None = None) -> ProjectType:
        """Auto-detect project type from files."""
        path = project_path or self.config.project_path
        for filename, ptype in _DETECT_MAP.items():
            if (path / filename).exists():
                self._project_type = ptype
                self._log(f"Detected project type: {ptype.value}")
                return ptype
        self._project_type = ProjectType.UNKNOWN
        self._log("Could not auto-detect project type")
        return ProjectType.UNKNOWN

    def generate_dockerfile(self, project_path: Path | None = None,
                            project_type: ProjectType | None = None) -> str:
        """Generate a Dockerfile for the project."""
        ptype = project_type or self._project_type
        if ptype == ProjectType.UNKNOWN:
            ptype = self.detect_project_type(project_path)

        template = _DOCKERFILE_TEMPLATES.get(ptype)
        if not template:
            self._log(f"No Dockerfile template for {ptype.value}")
            return ""

        return template.format(port=self.config.port)

    def generate_compose(self, service_name: str = "app",
                         project_type: ProjectType | None = None) -> str:
        """Generate docker-compose.yml content."""
        ptype = project_type or self._project_type
        port = self.config.port if ptype != ProjectType.STATIC else 80

        env_section = ""
        if self.config.env_vars:
            env_lines = "\n".join(f"      - {k}={v}" for k, v in self.config.env_vars.items())
            env_section = f"\n    environment:\n{env_lines}"

        return f"""\
services:
  {service_name}:
    build: .
    ports:
      - "{self.config.port}:{port}"
    restart: unless-stopped{env_section}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}{self.config.health_endpoint}"]
      interval: 30s
      timeout: 10s
      retries: 3
"""

    async def validate_project(self, project_path: Path | None = None) -> list[str]:
        """Validate project structure before deployment."""
        path = project_path or self.config.project_path
        issues: list[str] = []

        if not path.exists():
            issues.append(f"Project path does not exist: {path}")
            return issues

        # Check for entry point
        ptype = self._project_type or self.detect_project_type(path)

        entry_checks: dict[ProjectType, list[str]] = {
            ProjectType.PYTHON: ["main.py", "app.py", "__main__.py", "manage.py", "wsgi.py"],
            ProjectType.NODE: ["index.js", "server.js", "app.js", "dist/index.js"],
            ProjectType.RUST: ["src/main.rs"],
            ProjectType.GO: ["main.go", "cmd/main.go"],
            ProjectType.JAVA: ["src/main/java"],
        }

        if ptype in entry_checks:
            has_entry = any((path / e).exists() for e in entry_checks[ptype])
            if not has_entry:
                issues.append(f"No entry point found for {ptype.value} project")

        # Check for .env (should not be deployed)
        if (path / ".env").exists():
            issues.append("WARNING: .env file found — use environment variables instead")

        if (path / ".git").exists() and not (path / ".gitignore").exists():
            issues.append("WARNING: No .gitignore found")

        if issues:
            for issue in issues:
                self._log(f"Validation: {issue}")

        return issues

    async def run_build(self, project_path: Path | None = None) -> bool:
        """Run project build step."""
        path = project_path or self.config.project_path
        ptype = self._project_type or self.detect_project_type(path)

        build_commands: dict[ProjectType, list[str]] = {
            ProjectType.PYTHON: ["pip install -e ."],
            ProjectType.NODE: ["npm ci", "npm run build"],
            ProjectType.RUST: ["cargo build --release"],
            ProjectType.GO: ["go build ./..."],
            ProjectType.JAVA: ["mvn package -q"],
        }

        commands = build_commands.get(ptype, [])
        for cmd in commands:
            self._log(f"Build: {cmd}")
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=str(path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
                if proc.returncode != 0:
                    self._log(f"Build failed: {stderr.decode()[:500]}")
                    return False
            except asyncio.TimeoutError:
                self._log("Build timed out (300s)")
                return False
            except Exception as e:
                self._log(f"Build error: {e}")
                return False

        self._log("Build succeeded")
        return True

    async def deploy_docker(self, project_path: Path | None = None) -> DeployResult:
        """Full Docker deployment pipeline."""
        path = project_path or self.config.project_path

        # Stage 1: Detect
        self._stage = DeployStage.DETECT
        ptype = self.detect_project_type(path)
        if ptype == ProjectType.UNKNOWN:
            return self._fail("Cannot detect project type")

        # Stage 2: Validate
        self._stage = DeployStage.VALIDATE
        issues = await self.validate_project(path)
        errors = [i for i in issues if not i.startswith("WARNING")]
        if errors:
            return self._fail(f"Validation errors: {'; '.join(errors)}")

        # Stage 3: Generate Dockerfile if missing
        self._stage = DeployStage.BUILD
        dockerfile_path = path / "Dockerfile"
        if not dockerfile_path.exists():
            content = self.generate_dockerfile(path)
            if not content:
                return self._fail("Cannot generate Dockerfile")
            dockerfile_path.write_text(content, encoding="utf-8")
            self._log("Generated Dockerfile")

        compose_path = path / "docker-compose.yml"
        if not compose_path.exists():
            compose_content = self.generate_compose()
            compose_path.write_text(compose_content, encoding="utf-8")
            self._log("Generated docker-compose.yml")

        # Stage 4: Build container
        self._stage = DeployStage.CONTAINERIZE
        image_name = f"sharanbot-{self.config.deploy_id}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "build", "-t", image_name, ".",
                cwd=str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
            if proc.returncode != 0:
                return self._fail(f"Docker build failed: {stderr.decode()[:500]}")
        except FileNotFoundError:
            return self._fail("Docker not installed or not in PATH")
        except asyncio.TimeoutError:
            return self._fail("Docker build timed out (600s)")

        # Stage 5: Run container
        self._stage = DeployStage.DEPLOY
        container_name = f"sharanbot-{self.config.deploy_id}"
        port_map = f"{self.config.port}:{self.config.port}"
        env_args: list[str] = []
        for k, v in self.config.env_vars.items():
            env_args.extend(["-e", f"{k}={v}"])

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "-d", "--name", container_name,
                "-p", port_map, *env_args, image_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                return self._fail(f"Docker run failed: {stderr.decode()[:500]}")
            container_id = stdout.decode().strip()[:12]
        except Exception as e:
            return self._fail(f"Docker run error: {e}")

        # Stage 6: Health check
        self._stage = DeployStage.HEALTH_CHECK
        healthy = await self._health_check(container_name)
        if not healthy:
            # Rollback: stop and remove container
            await self._rollback_container(container_name)
            return self._fail("Health check failed — container rolled back")

        self._stage = DeployStage.DONE
        self._log(f"Deployment complete: {container_id}")
        return DeployResult(
            deploy_id=self.config.deploy_id,
            stage=DeployStage.DONE,
            success=True,
            project_type=ptype.value,
            container_id=container_id,
            logs=self._logs[:],
        )

    async def _health_check(self, container_name: str) -> bool:
        """Wait for container health check to pass."""
        import httpx

        url = f"http://localhost:{self.config.port}{self.config.health_endpoint}"
        deadline = asyncio.get_event_loop().time() + self.config.health_timeout

        async with httpx.AsyncClient(timeout=5) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await client.get(url)
                    if 200 <= resp.status_code < 400:
                        self._log("Health check passed")
                        return True
                except Exception:
                    pass
                await asyncio.sleep(2)

        self._log("Health check timed out")
        return False

    async def _rollback_container(self, container_name: str):
        """Stop and remove a failed container."""
        self._stage = DeployStage.ROLLED_BACK
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            self._log(f"Rolled back container {container_name}")
        except Exception as e:
            self._log(f"Rollback error: {e}")

    def _fail(self, error: str) -> DeployResult:
        self._stage = DeployStage.FAILED
        self._log(f"FAILED: {error}")
        return DeployResult(
            deploy_id=self.config.deploy_id,
            stage=DeployStage.FAILED,
            success=False,
            project_type=self._project_type.value,
            error=error,
            logs=self._logs[:],
        )

    def _log(self, msg: str):
        self._logs.append(msg)
        logger.debug("[deploy:{}] {}", self.config.deploy_id, msg)
