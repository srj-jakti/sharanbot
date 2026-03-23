"""Real-life verification of all audit fixes."""
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Verify core imports and initialization work
from sharanbot.agent.loop import AgentLoop
from sharanbot.agent.recovery import RecoveryEngine, ErrorCategory
from sharanbot.agent.context import ContextBuilder
from sharanbot.agent.memory import MemoryConsolidator
from sharanbot.agent.subagent import SubagentManager
from sharanbot.agent.team.dag import TaskDAG, TaskNode
from sharanbot.agent.team.autopilot import Autopilot
from sharanbot.channels.manager import ChannelManager
from sharanbot.bus.queue import MessageBus

print("=== Core Import Test: PASSED ===")

# Test 1: RecoveryEngine with pre-compiled regex
from sharanbot.agent.learning import LearningStore
from pathlib import Path
import tempfile
tmp_dir = Path(tempfile.mkdtemp())
learning = LearningStore(tmp_dir)
engine = RecoveryEngine(learning)
assert engine.classify_error("rate limit exceeded") == ErrorCategory.RATE_LIMIT
assert engine.classify_error("permission denied") == ErrorCategory.PERMISSION_ERROR
assert engine.classify_error("timeout") == ErrorCategory.TIMEOUT
assert engine.classify_error("500 internal server error") == ErrorCategory.API_ERROR
assert engine.classify_error("mcp server disconnected") == ErrorCategory.MCP_FAILURE
assert engine.classify_error("random tool error xyz") == ErrorCategory.TOOL_ERROR
print("=== Recovery Regex Test: PASSED ===")

# Test 2: DAG cycle detection on add_task
dag = TaskDAG()
dag.add_task(TaskNode(id="a", description="Task A", agent_profile="general"))
dag.add_task(TaskNode(id="b", description="Task B", agent_profile="general", depends_on=["a"]))
try:
    dag.add_task(TaskNode(id="c", description="Task C", agent_profile="general", depends_on=["b"]))
    print("=== DAG No-Cycle Test: PASSED ===")
except ValueError:
    print("=== DAG No-Cycle Test: FAILED (false positive) ===")
    sys.exit(1)

# Create a cycle: x -> y -> z -> x
dag2 = TaskDAG()
dag2.add_task(TaskNode(id="x", description="X", agent_profile="general"))
dag2.add_task(TaskNode(id="y", description="Y", agent_profile="general", depends_on=["x"]))
dag2.add_task(TaskNode(id="z", description="Z", agent_profile="general", depends_on=["y"]))
try:
    # Overwrite x to depend on z, creating x -> z -> y -> x cycle
    dag2.add_task(TaskNode(id="x", description="X", agent_profile="general", depends_on=["z"]))
    print("=== DAG Cycle Detection Test: FAILED (no ValueError raised) ===")
    sys.exit(1)
except ValueError as e:
    print(f"=== DAG Cycle Detection Test: PASSED ({e}) ===")

# Test 3: MemoryConsolidator uses real dict (not WeakValueDictionary)
# Verify at the source level since MemoryConsolidator requires many args
import inspect
source = inspect.getsource(MemoryConsolidator.__init__)
assert "dict[str, asyncio.Lock]" in source, "Should use regular dict"
assert "WeakValueDictionary" not in source, "Should NOT use WeakValueDictionary"
source_get_lock = inspect.getsource(MemoryConsolidator.get_lock)
assert "setdefault" not in source_get_lock, "Should not use setdefault (explicit check)"
print("=== Memory Lock Test: PASSED ===")

# Test 4: Autopilot token estimation
msgs = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello world!"},
    {"role": "assistant", "content": "Hi!", "tool_calls": [
        {"function": {"name": "read_file", "arguments": '{"path": "test.txt"}'}}
    ]},
]

class FakeAutopilot:
    _estimate_tokens = Autopilot._estimate_tokens

fake = FakeAutopilot()
tokens = fake._estimate_tokens(msgs)
assert tokens > 0, "Token estimate should be positive"
old_estimate = sum(len(str(m.get("content", ""))) for m in msgs) // 4
# New estimate should be different since it also counts tool calls
print(f"=== Token Estimation Test: PASSED (new={tokens}, old_method={old_estimate}) ===")

# Test 5: AgentLoop has session_locks attribute
import inspect
source = inspect.getsource(AgentLoop.__init__)
assert "_session_locks" in source, "AgentLoop should have _session_locks"
print("=== AgentLoop Session Lock: PASSED ===")

# Test 6: ContextBuilder has _MAX_IMAGE_SIZE
assert hasattr(ContextBuilder, "_MAX_IMAGE_SIZE")
assert ContextBuilder._MAX_IMAGE_SIZE == 20 * 1024 * 1024
print("=== Context Image Limit Test: PASSED ===")

# Test 7: Verify from_plan logs stripped dependencies
import logging
import io

log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
handler.setLevel(logging.WARNING)
dag_logger = logging.getLogger("sharanbot.agent.team.dag")
dag_logger.addHandler(handler)
dag_logger.setLevel(logging.WARNING)

plan = [
    {"id": "t1", "description": "First task"},
    {"id": "t2", "description": "Second task", "depends_on": ["t1", "nonexistent"]},
]
dag3 = TaskDAG.from_plan(plan)
log_output = log_stream.getvalue()
assert "nonexistent" in log_output or "stripped" in log_output, f"Expected warning about stripped deps, got: {log_output!r}"
print("=== DAG Stripped Deps Warning Test: PASSED ===")

print()
print("ALL VERIFICATION TESTS PASSED!")
