"""Integration test for GSD Worker spawning via WorkerManager."""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))


async def test_gsd_rpc_startup():
    """Test 1: Verify GSD starts in RPC mode and responds to commands."""
    gsd_loader = Path(__file__).resolve().parent / "sharanbot_worker" / "gsd" / "dist" / "loader.js"
    workspace = Path(os.path.expanduser("~/.sharanbot/workspace"))
    test_dir = workspace / "worker_test_project"
    test_dir.mkdir(parents=True, exist_ok=True)

    # Create GSD home with models config
    gsd_home = workspace / ".sharanbot_workers" / "test001"
    agent_dir = gsd_home / "agent"
    agent_dir.mkdir(parents=True, exist_ok=True)

    models_config = {
        "providers": {
            "sharanbot-llm": {
                "baseUrl": "https://api.kimi.com/coding/v1",
                "apiKey": "SHARANBOT_WORKER_API_KEY",
                "api": "openai-completions",
                "headers": {"User-Agent": "claude-code/1.0.0"},
                "models": [{
                    "id": "kimi-for-coding",
                    "name": "SharanBot Worker (kimi-for-coding)",
                    "contextWindow": 200000,
                    "maxTokens": 16384,
                }]
            }
        }
    }
    (agent_dir / "models.json").write_text(json.dumps(models_config, indent=2))

    env = dict(os.environ)
    env["GSD_HOME"] = str(gsd_home)
    env["SHARANBOT_WORKER_API_KEY"] = "sk-kimi-TdYsY4RgtSoI0TKf7lOzTATYIk4Pj77hTGxbVBaQcjd4jmepOxZ63gOVwU9R6es0"
    env["CUSTOM_OPENAI_API_KEY"] = env["SHARANBOT_WORKER_API_KEY"]
    env["GSD_NON_INTERACTIVE"] = "1"
    env["CI"] = "1"

    node = shutil.which("node")
    cmd = [node, str(gsd_loader), "--mode", "rpc"]

    print("=" * 60)
    print("TEST 1: GSD RPC Startup & get_state")
    print("=" * 60)
    print(f"  CWD: {test_dir}")
    print(f"  GSD_HOME: {gsd_home}")
    print(f"  GSD loader: {gsd_loader}")
    print()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(test_dir),
        env=env,
    )

    stdout_lines = []
    stderr_lines = []

    async def read_stdout():
        while proc.stdout and not proc.stdout.at_eof():
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                stdout_lines.append(line)

    async def read_stderr():
        while proc.stderr and not proc.stderr.at_eof():
            raw = await proc.stderr.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                stderr_lines.append(line)

    reader = asyncio.create_task(read_stdout())
    err_reader = asyncio.create_task(read_stderr())

    # Wait for startup
    await asyncio.sleep(5)

    # Send get_state
    if proc.returncode is None:
        print("[*] Sending get_state command...")
        msg = json.dumps({"type": "get_state", "id": "test1"}) + "\n"
        proc.stdin.write(msg.encode())
        await proc.stdin.drain()
        await asyncio.sleep(3)

    # Send get_available_models
    if proc.returncode is None:
        print("[*] Sending get_available_models...")
        msg = json.dumps({"type": "get_available_models", "id": "test2"}) + "\n"
        proc.stdin.write(msg.encode())
        await proc.stdin.drain()
        await asyncio.sleep(3)

    # Print results
    print()
    print(f"Process alive: {proc.returncode is None}")
    print(f"STDOUT lines ({len(stdout_lines)}):")
    for line in stdout_lines:
        try:
            parsed = json.loads(line)
            print(f"  [JSON] type={parsed.get('type', '?')} | {json.dumps(parsed)[:200]}")
        except json.JSONDecodeError:
            print(f"  [RAW] {line[:200]}")

    if stderr_lines:
        print(f"\nSTDERR lines ({len(stderr_lines)}):")
        for line in stderr_lines[-10:]:  # last 10
            print(f"  {line[:200]}")

    # Kill
    if proc.returncode is None:
        proc.kill()
        await proc.wait()

    reader.cancel()
    err_reader.cancel()

    # Check results
    state_found = any("get_state" in l for l in stdout_lines)
    print(f"\nget_state response found: {state_found}")
    return proc.returncode, stdout_lines, stderr_lines


async def test_worker_manager():
    """Test 2: Verify WorkerManager spawns and communicates with GSD."""
    from sharanbot.agent.worker import WorkerManager

    workspace = Path(os.path.expanduser("~/.sharanbot/workspace"))

    mgr = WorkerManager(
        workspace=workspace,
        api_key="sk-kimi-TdYsY4RgtSoI0TKf7lOzTATYIk4Pj77hTGxbVBaQcjd4jmepOxZ63gOVwU9R6es0",
        api_base="https://api.kimi.com/coding/v1",
        model="kimi-for-coding",
        extra_headers={"User-Agent": "claude-code/1.0.0"},
    )

    print()
    print("=" * 60)
    print("TEST 2: WorkerManager spawn & status")
    print("=" * 60)

    # Check status with no workers
    status = mgr.status()
    print(f"[*] Initial status: {status}")
    assert "No active workers" in status, f"Expected no workers, got: {status}"

    # Spawn a worker with a simple task
    test_project = workspace / "worker_test_project"
    test_project.mkdir(parents=True, exist_ok=True)

    result = await mgr.spawn(
        task="List the files in the current directory and create a file called hello.txt with the text 'Hello from GSD Worker!'",
        label="test-hello",
        project_dir=str(test_project),
    )
    print(f"[*] Spawn result: {result}")

    # Check status
    await asyncio.sleep(2)
    status = mgr.status()
    print(f"[*] Status after spawn: {status}")

    # Get specific worker status
    workers = list(mgr._workers.values())
    if workers:
        w = workers[0]
        print(f"[*] Worker ID: {w.id}")
        print(f"[*] Worker status: {w.status}")
        print(f"[*] Worker label: {w.label}")
        print(f"[*] Worker project_dir: {w.project_dir}")
        print(f"[*] Worker in workspace: {str(workspace) in str(w.project_dir)}")
        print(f"[*] Messages received: {len(w.messages)}")

        # Wait a bit for messages
        await asyncio.sleep(8)
        print(f"[*] Messages after wait: {len(w.messages)}")
        for msg in w.messages[-5:]:
            msg_type = msg.get("type", "?")
            content = str(msg)[:150]
            print(f"    [{msg_type}] {content}")

    # Stop all workers
    await mgr.stop_all()
    print(f"[*] All workers stopped")

    # Verify workspace is SharanBot's
    print(f"\n[*] SharanBot workspace: {workspace}")
    print(f"[*] Worker project dir was inside workspace: {workers[0].project_dir if workers else 'N/A'}")

    return True


async def test_worker_tools():
    """Test 3: Verify worker tools work correctly."""
    from sharanbot.agent.tools.worker import (
        WorkerStartTool,
        WorkerStatusTool,
        WorkerSteerTool,
        WorkerStopTool,
    )
    from sharanbot.agent.worker import WorkerManager

    workspace = Path(os.path.expanduser("~/.sharanbot/workspace"))
    mgr = WorkerManager(
        workspace=workspace,
        api_key="sk-kimi-TdYsY4RgtSoI0TKf7lOzTATYIk4Pj77hTGxbVBaQcjd4jmepOxZ63gOVwU9R6es0",
        api_base="https://api.kimi.com/coding/v1",
        model="kimi-for-coding",
        extra_headers={"User-Agent": "claude-code/1.0.0"},
    )

    print()
    print("=" * 60)
    print("TEST 3: Worker Tools API")
    print("=" * 60)

    start_tool = WorkerStartTool(manager=mgr)
    status_tool = WorkerStatusTool(manager=mgr)
    steer_tool = WorkerSteerTool(manager=mgr)
    stop_tool = WorkerStopTool(manager=mgr)

    # Check tool metadata
    print(f"[*] start_tool.name: {start_tool.name}")
    print(f"[*] status_tool.name: {status_tool.name}")
    print(f"[*] steer_tool.name: {steer_tool.name}")
    print(f"[*] stop_tool.name: {stop_tool.name}")

    assert start_tool.name == "worker_start"
    assert status_tool.name == "worker_status"
    assert steer_tool.name == "worker_steer"
    assert stop_tool.name == "worker_stop"

    # Check parameters
    start_params = start_tool.parameters
    assert "task" in start_params["properties"]
    assert "label" in start_params["properties"]
    assert "project_dir" in start_params["properties"]
    print("[*] Tool parameter schemas correct")

    # Test status with no workers
    result = await status_tool.execute()
    assert "No active workers" in result
    print(f"[*] Empty status: {result}")

    # Start a worker
    result = await start_tool.execute(
        task="Create a simple Python calculator with add, subtract, multiply, divide functions",
        label="calc-builder",
    )
    print(f"[*] Start result: {result[:100]}...")
    assert "calc-builder" in result
    assert "started" in result.lower()

    # Wait for startup
    await asyncio.sleep(5)

    # Check status
    result = await status_tool.execute()
    print(f"[*] All status: {result}")
    assert "calc-builder" in result

    # Get the worker ID from the workers dict
    worker_id = list(mgr._workers.keys())[0] if mgr._workers else None
    if worker_id:
        # Check specific status
        result = await status_tool.execute(worker_id=worker_id)
        print(f"[*] Worker status: {result[:200]}")

        # Steer the worker
        result = await steer_tool.execute(
            worker_id=worker_id,
            message="Also add a power function",
        )
        print(f"[*] Steer result: {result}")

        await asyncio.sleep(3)

        # Stop the worker
        result = await stop_tool.execute(worker_id=worker_id)
        print(f"[*] Stop result: {result}")

    await mgr.stop_all()
    print("[*] Test 3 complete")
    return True


async def main():
    print("GSD Worker Integration Tests")
    print("=" * 60)

    try:
        # Test 1: Raw GSD RPC
        rc, stdout, stderr = await test_gsd_rpc_startup()
        test1_pass = rc is None or len(stdout) > 0
        print(f"\nTEST 1 {'PASS' if test1_pass else 'FAIL'}")
    except Exception as e:
        print(f"\nTEST 1 FAIL: {e}")
        import traceback
        traceback.print_exc()
        test1_pass = False

    try:
        # Test 2: WorkerManager
        test2_pass = await test_worker_manager()
        print(f"\nTEST 2 PASS")
    except Exception as e:
        print(f"\nTEST 2 FAIL: {e}")
        import traceback
        traceback.print_exc()
        test2_pass = False

    try:
        # Test 3: Worker Tools
        test3_pass = await test_worker_tools()
        print(f"\nTEST 3 PASS")
    except Exception as e:
        print(f"\nTEST 3 FAIL: {e}")
        import traceback
        traceback.print_exc()
        test3_pass = False

    print()
    print("=" * 60)
    print("RESULTS:")
    print(f"  Test 1 (GSD RPC Startup):    {'PASS' if test1_pass else 'FAIL'}")
    print(f"  Test 2 (WorkerManager):       {'PASS' if test2_pass else 'FAIL'}")
    print(f"  Test 3 (Worker Tools):        {'PASS' if test3_pass else 'FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
