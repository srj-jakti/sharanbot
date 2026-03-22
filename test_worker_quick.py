"""Quick test: spawn a GSD worker to build a project and verify it works."""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


async def main():
    from sharanbot.agent.worker import WorkerManager

    workspace = Path(os.path.expanduser("~/.sharanbot/workspace"))
    test_project = workspace / "calculator_app"
    test_project.mkdir(parents=True, exist_ok=True)

    mgr = WorkerManager(
        workspace=workspace,
        api_key="sk-kimi-TdYsY4RgtSoI0TKf7lOzTATYIk4Pj77hTGxbVBaQcjd4jmepOxZ63gOVwU9R6es0",
        api_base="https://api.kimi.com/coding/v1",
        model="kimi-for-coding",
        extra_headers={"User-Agent": "claude-code/1.0.0"},
    )

    print("=" * 60)
    print("GSD Worker Test: Build Calculator App")
    print("=" * 60)
    print(f"Workspace: {workspace}")
    print(f"Project dir: {test_project}")
    print(f"Worker builds inside SharanBot workspace: YES")
    print()

    result = await mgr.spawn(
        task=(
            "Create a simple Python calculator application. "
            "Create a file called calculator.py with functions for add, subtract, multiply, divide. "
            "Also create a test_calculator.py with basic tests. "
            "Keep it simple - just the core functions and tests."
        ),
        label="calc-builder",
        project_dir=str(test_project),
    )
    print(f"Spawn: {result}")
    print()

    # Monitor the worker
    workers = list(mgr._workers.values())
    if not workers:
        print("ERROR: No workers spawned!")
        return

    w = workers[0]
    print(f"Worker ID: {w.id}")
    print(f"Worker dir: {w.project_dir}")
    print()

    # Wait and poll status
    for i in range(6):
        await asyncio.sleep(5)
        print(f"--- Poll {i+1} (t={5*(i+1)}s) ---")
        print(f"Status: {w.status}")
        print(f"Messages: {len(w.messages)}")
        
        # Show recent events
        new_msgs = w.messages[-(min(5, len(w.messages))):]
        for msg in new_msgs:
            msg_type = msg.get("type", "?")
            if msg_type == "content":
                text = ""
                for c in msg.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "text":
                        text += c.get("text", "")
                if text:
                    print(f"  [content] {text[:200]}")
            elif msg_type == "tool_use":
                tool = msg.get("tool", msg.get("name", "?"))
                print(f"  [tool_use] {tool}")
            elif msg_type in ("turn_end", "agent_end"):
                print(f"  [{msg_type}]")
            elif msg_type == "message_start":
                role = msg.get("message", {}).get("role", "?")
                print(f"  [message_start] role={role}")
            elif msg_type == "message_end":
                pass  # skip noise
            else:
                content = str(msg)[:150]
                print(f"  [{msg_type}] {content}")
        
        if w.status != "running":
            break
        print()

    # Check what files were created
    print()
    print("=" * 60)
    print("Files in project dir after worker:")
    for f in sorted(test_project.rglob("*")):
        if f.is_file() and ".bg-shell" not in str(f) and ".gsd" not in str(f):
            print(f"  {f.relative_to(test_project)}")

    # Show calculator.py if created
    calc_file = test_project / "calculator.py"
    if calc_file.exists():
        print()
        print("calculator.py content:")
        print(calc_file.read_text(encoding="utf-8")[:500])

    await mgr.stop_all()
    print()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
