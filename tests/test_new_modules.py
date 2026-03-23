"""Tests for new agent modules — vector memory, memory extractor,
adaptive learning, proof of work, parallel engine, project tracker, model router.
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sharanbot.agent.vector_memory import VectorMemoryStore, _hash_embed
from sharanbot.agent.memory_extractor import extract_turn_memories, detect_user_correction
from sharanbot.agent.adaptive_learning import AdaptiveLearningEngine
from sharanbot.agent.proof_of_work import ProofOfWork, load_proof_of_work
from sharanbot.agent.project_tracker import ProjectTracker
from sharanbot.agent.model_router import ModelRouter


# ── VectorMemoryStore ────────────────────────────────────────────────

class TestVectorMemory:
    @pytest.mark.asyncio
    async def test_add_and_search(self, tmp_path):
        store = VectorMemoryStore(workspace=tmp_path)
        await store.add("m1", "Python is great for scripting", "fact")
        await store.add("m2", "JavaScript runs in browsers", "fact")
        vec = _hash_embed("programming language")
        results = store.search(vec, top_k=2)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_deduplication(self, tmp_path):
        store = VectorMemoryStore(workspace=tmp_path)
        await store.add("m1", "The sky is blue", "fact")
        await store.add("m2", "The sky is blue", "fact")  # Near duplicate
        assert len(store) <= 1  # Should not create duplicate

    @pytest.mark.asyncio
    async def test_get_context(self, tmp_path):
        store = VectorMemoryStore(workspace=tmp_path)
        await store.add("m1", "User prefers dark mode", "preference")
        ctx = store.get_context()
        assert isinstance(ctx, str)

    @pytest.mark.asyncio
    async def test_remove(self, tmp_path):
        store = VectorMemoryStore(workspace=tmp_path)
        await store.add("m1", "temporary entry", "fact")
        assert store.remove("m1")
        assert len(store) == 0


# ── MemoryExtractor ──────────────────────────────────────────────────

class TestMemoryExtractor:
    def test_explicit_remember(self):
        memories = extract_turn_memories(
            "Remember that I prefer Python over JavaScript",
            "",
        )
        assert len(memories) >= 1
        assert any(m.confidence >= 0.9 for m in memories)

    def test_explicit_forget(self):
        memories = extract_turn_memories(
            "Forget that I like dark mode",
            "",
        )
        assert len(memories) >= 1
        assert any("dark mode" in m.text for m in memories)

    def test_implicit_preference(self):
        memories = extract_turn_memories(
            "I always use VS Code for development",
            "",
        )
        assert len(memories) >= 1

    def test_detect_correction(self):
        correction = detect_user_correction(
            "No, that's wrong. The API uses POST not GET",
        )
        assert correction is True

    def test_no_extraction_for_questions(self):
        memories = extract_turn_memories(
            "What is Python?",
            "",
        )
        # Questions shouldn't produce implicit memories
        assert len(memories) == 0 or all(m.confidence < 0.9 for m in memories)

    def test_max_implicit_limit(self):
        memories = extract_turn_memories(
            "I prefer Python, I always use Linux, I love dark mode, I work at Google, I'm a senior dev",
            "",
        )
        # Should cap implicit memories
        implicit = [m for m in memories if not m.is_explicit]
        assert len(implicit) <= 2


# ── AdaptiveLearningEngine ──────────────────────────────────────────

class TestAdaptiveLearning:
    def test_project_profile(self, tmp_path):
        engine = AdaptiveLearningEngine(workspace=tmp_path)
        engine.learn_project_type("python")
        ctx = engine.get_project_context("python")
        assert isinstance(ctx, str)

    def test_record_mistake(self, tmp_path):
        engine = AdaptiveLearningEngine(workspace=tmp_path)
        engine.record_mistake(
            context="API development",
            mistake="Used wrong API endpoint",
            correction="Always read API docs first",
        )
        ctx = engine.get_mistake_context()
        assert "wrong API" in ctx

    def test_user_preference(self, tmp_path):
        engine = AdaptiveLearningEngine(workspace=tmp_path)
        engine.update_preference("editor", "vscode", confidence_boost=0.4)
        prefs = engine.get_preferences_context()
        assert "vscode" in prefs

    def test_growth_stats(self, tmp_path):
        engine = AdaptiveLearningEngine(workspace=tmp_path)
        engine.record_mistake("ctx", "err", "fix")
        engine.update_preference("x", "y")
        summary = engine.get_growth_summary()
        assert isinstance(summary, str)


# ── ProofOfWork ──────────────────────────────────────────────────────

class TestProofOfWork:
    def test_record_action(self, tmp_path):
        pow_obj = ProofOfWork(task_id="test-1")
        pow_obj.record("tool_call", "ran exec: ls")
        assert len(pow_obj.entries) == 1

    def test_record_file_operations(self, tmp_path):
        pow_obj = ProofOfWork(task_id="test-2")
        pow_obj.record("file_write", "Created main.py")
        pow_obj.record("file_modify", "Updated config.py")
        assert len(pow_obj.entries) == 2

    def test_complete_success(self, tmp_path):
        pow_obj = ProofOfWork(task_id="test-3")
        pow_obj.record("tool_call", "ran tests")
        pow_obj.record("file_write", "Created output")
        result = pow_obj.complete(success=True)
        assert result is True
        assert pow_obj.status == "verified"

    def test_complete_failure(self, tmp_path):
        pow_obj = ProofOfWork(task_id="test-4")
        pow_obj.record("error", "Something crashed")
        result = pow_obj.complete(success=False)
        assert pow_obj.status == "failed"

    def test_persistence(self, tmp_path):
        pow_obj = ProofOfWork(task_id="test-5")
        pow_obj.record("tool_call", "action 1")
        pow_obj.save(workspace=tmp_path)
        loaded = load_proof_of_work(workspace=tmp_path, task_id="test-5")
        assert loaded is not None
        assert len(loaded.entries) == 1

    def test_summary(self):
        pow_obj = ProofOfWork(task_id="test-6", task_description="Build app")
        pow_obj.record("tool_call", "did something")
        s = pow_obj.summary
        assert "test-6" in s


# ── ProjectTracker ───────────────────────────────────────────────────

class TestProjectTracker:
    def test_scan_workspace(self, tmp_path):
        # Create some project indicators
        proj_dir = tmp_path / "my-app"
        proj_dir.mkdir()
        (proj_dir / "package.json").write_text('{"name": "my-app"}')

        tracker = ProjectTracker(workspace=tmp_path)
        projects = tracker.scan()
        assert len(projects) >= 1

    def test_active_projects(self, tmp_path):
        proj_dir = tmp_path / "old-app"
        proj_dir.mkdir()
        (proj_dir / "pyproject.toml").write_text("[project]")

        tracker = ProjectTracker(workspace=tmp_path)
        tracker.scan()
        active = tracker.active_projects
        assert len(active) >= 1

    def test_get_context(self, tmp_path):
        proj_dir = tmp_path / "app"
        proj_dir.mkdir()
        (proj_dir / "Cargo.toml").write_text('[package]\nname = "app"')
        tracker = ProjectTracker(workspace=tmp_path)
        tracker.scan()
        ctx = tracker.get_context()
        assert isinstance(ctx, str)

    def test_set_goal(self, tmp_path):
        proj_dir = tmp_path / "goal-app"
        proj_dir.mkdir()
        (proj_dir / "package.json").write_text("{}")
        tracker = ProjectTracker(workspace=tmp_path)
        tracker.scan()
        # set_goal uses relative path key
        result = tracker.set_goal("goal-app", "Ship by Friday")
        assert result is True


# ── ModelRouter ──────────────────────────────────────────────────────

class TestModelRouter:
    def _make_router(self):
        router = ModelRouter()
        router.add_tier("fast", "gpt-3.5-turbo", cost_per_1k=0.001, max_complexity="simple")
        router.add_tier("balanced", "gpt-4", cost_per_1k=0.03, max_complexity="moderate")
        router.add_tier("powerful", "claude-3-opus", cost_per_1k=0.06, max_complexity="complex")
        return router

    def test_route_task(self):
        router = self._make_router()
        model = router.route("What is 2+2?")
        assert model is not None
        assert isinstance(model, str)

    def test_route_complex(self):
        router = self._make_router()
        model = router.route(
            "Build a distributed microservice architecture with "
            "event sourcing, CQRS, and Kubernetes orchestration"
        )
        assert isinstance(model, str)

    def test_stats(self):
        router = self._make_router()
        router.route("hello")
        router.route("build complex system with deployment pipeline")
        stats = router.stats
        assert isinstance(stats, dict)


# ── New Tools ────────────────────────────────────────────────────────

class TestCreateSoftwareTool:
    @pytest.mark.asyncio
    async def test_create_project(self, tmp_path):
        from sharanbot.agent.tools.create_software import CreateSoftwareTool
        tool = CreateSoftwareTool()
        result = await tool.execute(
            project_path=str(tmp_path / "myapp"),
            files={
                "main.py": "print('hello')",
                "README.md": "# My App",
                "src/utils.py": "def add(a, b): return a + b",
            },
        )
        assert "Created 3 files" in result
        assert (tmp_path / "myapp" / "main.py").exists()
        assert (tmp_path / "myapp" / "src" / "utils.py").exists()

    @pytest.mark.asyncio
    async def test_boilerplate_generation(self, tmp_path):
        from sharanbot.agent.tools.create_software import CreateSoftwareTool
        tool = CreateSoftwareTool()
        result = await tool.execute(
            project_path=str(tmp_path / "pyapp"),
            files={"app.py": "print('app')"},
            project_type="python",
            project_name="pyapp",
        )
        assert (tmp_path / "pyapp" / ".gitignore").exists()
        assert (tmp_path / "pyapp" / "pyproject.toml").exists()
        assert (tmp_path / "pyapp" / "README.md").exists()

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, tmp_path):
        from sharanbot.agent.tools.create_software import CreateSoftwareTool
        tool = CreateSoftwareTool()
        result = await tool.execute(
            project_path=str(tmp_path / "safe"),
            files={"../outside.py": "evil code"},
        )
        assert "path traversal" in result.lower() or "Skipped" in result


class TestSpawnDevTeamTool:
    @pytest.mark.asyncio
    async def test_spawn_basic(self, tmp_path):
        from sharanbot.agent.tools.spawn_dev_team import SpawnDevTeamTool
        tool = SpawnDevTeamTool()
        result = await tool.execute(
            description="Build a REST API for a todo app",
            project_path=str(tmp_path / "todo-api"),
        )
        assert "initialized" in result.lower() or "Project" in result


# ── Enhanced Recovery ────────────────────────────────────────────────

class TestRecoveryEnhancements:
    def test_package_name_map(self):
        from sharanbot.agent.recovery import RecoveryEngine
        assert RecoveryEngine.PACKAGE_NAME_MAP["cv2"] == "opencv-python"
        assert RecoveryEngine.PACKAGE_NAME_MAP["PIL"] == "Pillow"
        assert RecoveryEngine.PACKAGE_NAME_MAP["sklearn"] == "scikit-learn"

    def test_import_error_classification(self, tmp_path):
        from sharanbot.agent.recovery import RecoveryEngine, ErrorCategory
        from sharanbot.agent.learning import LearningStore
        ls = LearningStore(workspace=tmp_path)
        engine = RecoveryEngine(learning=ls)
        cat = engine.classify_error("ModuleNotFoundError: No module named 'cv2'")
        assert cat == ErrorCategory.IMPORT_ERROR


# ── Enhanced Learning ────────────────────────────────────────────────

class TestLearningEnhancements:
    def test_record_success(self, tmp_path):
        from sharanbot.agent.learning import LearningStore
        ls = LearningStore(workspace=tmp_path)
        ls.record_success(
            "file_creation",
            tool_chain=["write_file", "exec"],
            description="Create and run a script",
        )
        patterns = ls.get_positive_patterns()
        assert len(patterns) == 1

    def test_best_approach(self, tmp_path):
        from sharanbot.agent.learning import LearningStore
        ls = LearningStore(workspace=tmp_path)
        ls.record_success(
            "web_scraping",
            tool_chain=["web_fetch", "write_file"],
            confidence=0.9,
        )
        chain = ls.get_best_approach("web_scraping")
        assert chain == ["web_fetch", "write_file"]

    def test_context_for_prompt(self, tmp_path):
        from sharanbot.agent.learning import LearningStore
        ls = LearningStore(workspace=tmp_path)
        ls.record_success(
            "testing",
            tool_chain=["exec"],
            description="Run pytest",
            confidence=0.8,
        )
        ctx = ls.get_context_for_prompt()
        assert "pytest" in ctx or "testing" in ctx

    def test_confidence_increases(self, tmp_path):
        from sharanbot.agent.learning import LearningStore
        ls = LearningStore(workspace=tmp_path)
        ls.record_success("deploy", ["exec"], confidence=0.5)
        ls.record_success("deploy", ["exec"])  # Second time
        entry = ls.lookup("positive:deploy")
        assert entry.metadata["confidence"] > 0.5
