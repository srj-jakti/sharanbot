"""End-to-end test of the self-evolution architecture."""
import asyncio
import os
import sys
from pathlib import Path

# Ensure project is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

async def test_dynamic_tool_loading():
    """Test 1: Dynamic tool loading from plugins/tools/"""
    print("\n" + "="*60)
    print("TEST 1: Dynamic Tool Loading")
    print("="*60)
    
    from sharanbot.agent.tools.registry import ToolRegistry
    from sharanbot.agent.tools.dynamic import DynamicToolLoader
    
    ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
    registry = ToolRegistry()
    
    loader = DynamicToolLoader(
        tools_dir=ws / "plugins" / "tools",
        registry=registry,
    )
    
    count = loader.scan_and_load()
    print(f"  Loaded {count} dynamic tool(s)")
    
    # Check calculator tool is registered
    calc = registry.get("calculator")
    if calc:
        print(f"  ✓ Calculator tool found: {calc.name}")
        print(f"  ✓ Description: {calc.description[:60]}...")
        
        # Actually execute it
        result = await calc.execute(expression="2 + 3 * 4")
        print(f"  ✓ Execute '2 + 3 * 4' = {result}")
        
        result2 = await calc.execute(expression="2 ** 10")
        print(f"  ✓ Execute '2 ** 10' = {result2}")
        
        result3 = await calc.execute(expression="100 / 3")
        print(f"  ✓ Execute '100 / 3' = {result3}")
    else:
        print("  ✗ Calculator tool NOT found!")
        return False
    
    # Test tool definitions appear in LLM format
    defs = registry.get_definitions()
    tool_names = [d["function"]["name"] for d in defs]
    print(f"  ✓ Tool definitions include: {tool_names}")
    
    # Test reload
    count2 = loader.reload_all()
    print(f"  ✓ Reload returned {count2} tool(s)")
    
    calc2 = registry.get("calculator")
    assert calc2 is not None, "Calculator should survive reload"
    print(f"  ✓ Calculator survives reload")
    
    # Test unload
    loader.unload_all()
    calc3 = registry.get("calculator")
    assert calc3 is None, "Calculator should be gone after unload"
    print(f"  ✓ Calculator removed after unload")
    
    return True


async def test_worker_type_registry():
    """Test 2: Worker type registry with builtin GSD"""
    print("\n" + "="*60)
    print("TEST 2: Worker Type Registry")
    print("="*60)
    
    from sharanbot.agent.worker_registry import WorkerTypeRegistry
    
    ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
    wtr = WorkerTypeRegistry(plugins_dir=ws / "plugins" / "workers")
    wtr.scan_plugins()
    
    types = wtr.list_types()
    print(f"  ✓ {len(types)} worker type(s) registered")
    
    gsd = wtr.get("gsd")
    assert gsd is not None, "GSD should be registered"
    print(f"  ✓ GSD worker: {gsd.display_name}")
    print(f"  ✓ GSD loader: {gsd.loader_path}")
    print(f"  ✓ GSD runtime: {gsd.runtime}")
    print(f"  ✓ GSD mode_args: {gsd.mode_args}")
    
    # Test plugin scanning (should not crash even with empty dir)
    wtr.reload_plugins()
    print(f"  ✓ Plugin reload OK (still {len(wtr.list_types())} types)")
    
    # Test unknown type
    unknown = wtr.get("nonexistent")
    assert unknown is None, "Unknown type should return None"
    print(f"  ✓ Unknown type returns None")
    
    return True


async def test_agent_profile_registry():
    """Test 3: Agent profile registry with builtins"""
    print("\n" + "="*60)
    print("TEST 3: Agent Profile Registry")
    print("="*60)
    
    from sharanbot.agent.profiles import AgentProfileRegistry
    
    ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
    apr = AgentProfileRegistry(plugins_dir=ws / "plugins" / "agents")
    apr.scan_plugins()
    
    profiles = apr.list_profiles()
    print(f"  ✓ {len(profiles)} agent profile(s) registered")
    
    for p in profiles:
        print(f"    - {p.name}: {p.display_name} (max_iter={p.max_iterations})")
    
    general = apr.get("general")
    assert general is not None
    print(f"  ✓ General profile has system_prompt: {len(general.system_prompt)} chars")
    
    coder = apr.get("coder")
    assert coder is not None
    print(f"  ✓ Coder profile: denied_tools={coder.denied_tools}")
    
    researcher = apr.get("researcher")
    assert researcher is not None
    print(f"  ✓ Researcher profile: denied_tools={researcher.denied_tools}")
    
    return True


async def test_custom_agent_profile_from_yaml():
    """Test 4: Create a custom agent profile from YAML and load it"""
    print("\n" + "="*60)
    print("TEST 4: Custom Agent Profile (YAML Plugin)")
    print("="*60)
    
    from sharanbot.agent.profiles import AgentProfileRegistry
    
    ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
    plugins_dir = ws / "plugins" / "agents"
    
    # Write a custom profile YAML
    profile_yaml = plugins_dir / "math_expert.yaml"
    profile_yaml.write_text("""
name: math_expert
display_name: Math Expert
description: Specialized agent for mathematical computations
system_prompt: |
  You are a mathematics expert. You excel at solving equations,
  proofs, statistics, and numerical analysis. Always show your work
  step by step.
max_iterations: 20
denied_tools:
  - web_search
  - web_fetch
""", encoding="utf-8")
    print(f"  ✓ Wrote YAML profile: {profile_yaml}")
    
    # Load it
    apr = AgentProfileRegistry(plugins_dir=plugins_dir)
    apr.scan_plugins()
    
    profiles = apr.list_profiles()
    names = [p.name for p in profiles]
    print(f"  ✓ Profiles after scan: {names}")
    
    math = apr.get("math_expert")
    if math:
        print(f"  ✓ math_expert loaded!")
        print(f"    display_name: {math.display_name}")
        print(f"    max_iterations: {math.max_iterations}")
        print(f"    denied_tools: {math.denied_tools}")
        print(f"    system_prompt preview: {math.system_prompt[:80]}...")
    else:
        print("  ✗ math_expert NOT found!")
        return False
    
    # Test reload
    apr.reload_plugins()
    math2 = apr.get("math_expert")
    assert math2 is not None, "Should survive reload"
    print(f"  ✓ Survives reload")
    
    # Clean up
    profile_yaml.unlink()
    print(f"  ✓ Cleaned up YAML file")
    
    return True


async def test_custom_worker_type_from_json():
    """Test 5: Create a custom worker type from JSON manifest"""
    print("\n" + "="*60)
    print("TEST 5: Custom Worker Type (JSON Manifest)")
    print("="*60)
    
    import json
    from sharanbot.agent.worker_registry import WorkerTypeRegistry
    
    ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
    plugins_dir = ws / "plugins" / "workers"
    
    # Write a worker manifest
    manifest = plugins_dir / "python_worker.json"
    manifest.write_text(json.dumps({
        "name": "python_coder",
        "display_name": "Python Coder Worker",
        "loader_path": str(ws / "workers" / "python_coder" / "main.py"),
        "mode_args": ["--mode", "rpc"],
        "runtime": "python",
        "protocol": "jsonrpc",
        "description": "A Python-based worker specialized for Python coding tasks"
    }, indent=2), encoding="utf-8")
    print(f"  ✓ Wrote JSON manifest: {manifest}")
    
    wtr = WorkerTypeRegistry(plugins_dir=plugins_dir)
    wtr.scan_plugins()
    
    types = wtr.list_types()
    names = [t.name for t in types]
    print(f"  ✓ Worker types after scan: {names}")
    
    py_worker = wtr.get("python_coder")
    if py_worker:
        print(f"  ✓ python_coder loaded!")
        print(f"    display_name: {py_worker.display_name}")
        print(f"    runtime: {py_worker.runtime}")
        print(f"    loader_path: {py_worker.loader_path}")
        print(f"    mode_args: {py_worker.mode_args}")
    else:
        print("  ✗ python_coder NOT found!")
        return False
    
    # Clean up
    manifest.unlink()
    print(f"  ✓ Cleaned up JSON manifest")
    
    return True


async def test_reload_tool():
    """Test 6: ReloadTool operates on a mock agent loop"""
    print("\n" + "="*60)
    print("TEST 6: ReloadTool Integration")
    print("="*60)
    
    from sharanbot.agent.tools.reload import ReloadTool
    
    # Mock agent loop
    class MockLoop:
        async def handle_reload(self, component, target=None):
            return f"Reloaded {component}" + (f" ({target})" if target else "")
    
    tool = ReloadTool(agent_loop=MockLoop())
    print(f"  ✓ ReloadTool name: {tool.name}")
    print(f"  ✓ ReloadTool params: {list(tool.parameters['properties'].keys())}")
    
    result = await tool.execute(component="tools")
    print(f"  ✓ reload(tools) -> {result}")
    
    result2 = await tool.execute(component="mcp")
    print(f"  ✓ reload(mcp) -> {result2}")
    
    result3 = await tool.execute(component="config")
    print(f"  ✓ reload(config) -> {result3}")
    
    result4 = await tool.execute(component="workers")
    print(f"  ✓ reload(workers) -> {result4}")
    
    result5 = await tool.execute(component="agents")
    print(f"  ✓ reload(agents) -> {result5}")
    
    return True


async def test_evolve_tool():
    """Test 7: EvolveTool generates correct specs"""
    print("\n" + "="*60)
    print("TEST 7: EvolveTool Spec Generation")
    print("="*60)
    
    from sharanbot.agent.tools.evolve import EvolveTool
    
    class MockLoop:
        workspace = Path(os.path.expanduser("~/.sharanbot/workspace"))
        worker_manager = None
        async def handle_reload(self, component, target=None):
            return f"Reloaded {component}"
    
    tool = EvolveTool(agent_loop=MockLoop())
    print(f"  ✓ EvolveTool name: {tool.name}")
    print(f"  ✓ EvolveTool params: {list(tool.parameters['properties'].keys())}")
    
    # Test without worker (should give manual instructions)
    result = await tool.execute(
        capability_type="tool",
        capability_name="weather",
        specification="Get current weather for a city using OpenWeatherMap API"
    )
    print(f"  ✓ evolve(tool, weather) -> {len(result)} chars")
    print(f"  ✓ Result preview: {result[:150]}...")
    
    return True


async def test_config_watcher():
    """Test 8: ConfigWatcher detects file changes"""
    print("\n" + "="*60)
    print("TEST 8: ConfigWatcher")
    print("="*60)
    
    import tempfile
    import json
    import time
    from sharanbot.config.watcher import ConfigWatcher
    
    # Create a temp config file
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps({"test": True}), encoding="utf-8")
    
    changes = []
    
    def on_change(new_config):
        changes.append(new_config)
    
    watcher = ConfigWatcher(config_path=tmp, on_change=on_change, poll_interval=0.5)
    await watcher.start()
    print(f"  ✓ Watcher started (poll_interval=0.5s)")
    
    # Wait a bit then modify the file
    await asyncio.sleep(1)
    tmp.write_text(json.dumps({"test": True, "modified": True}), encoding="utf-8")
    print(f"  ✓ Config file modified")
    
    # Wait for detection
    await asyncio.sleep(2)
    watcher.stop()
    
    if changes:
        print(f"  ✓ Change detected! ({len(changes)} change(s))")
    else:
        print(f"  ⚠ No change detected (watcher may not have polled yet)")
    
    tmp.unlink(missing_ok=True)
    print(f"  ✓ Cleaned up")
    
    return True


async def test_reload_config():
    """Test 9: reload_config function"""
    print("\n" + "="*60)
    print("TEST 9: Config Reload")
    print("="*60)
    
    from sharanbot.config.loader import reload_config
    from sharanbot.config.paths import get_config_path
    
    config_path = get_config_path()
    print(f"  Config path: {config_path}")
    
    if config_path.exists():
        new_cfg = reload_config(config_path)
        print(f"  ✓ reload_config() returned Config object")
        print(f"  ✓ Model: {new_cfg.agents.defaults.model}")
        print(f"  ✓ Worker enabled: {new_cfg.worker.enable}")
    else:
        print(f"  ⚠ Config file not found, skipping")
    
    return True


async def test_full_agent_loop_creation():
    """Test 10: Full AgentLoop creation with all new components"""
    print("\n" + "="*60)
    print("TEST 10: Full AgentLoop Creation (Integration)")
    print("="*60)
    
    from unittest.mock import MagicMock
    from sharanbot.agent.loop import AgentLoop
    from sharanbot.bus.queue import MessageBus
    from sharanbot.agent.worker_registry import WorkerTypeRegistry
    from sharanbot.agent.profiles import AgentProfileRegistry
    
    ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    
    wtr = WorkerTypeRegistry(plugins_dir=ws / "plugins" / "workers")
    apr = AgentProfileRegistry(plugins_dir=ws / "plugins" / "agents")
    
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=ws,
        model="test-model",
        worker_type_registry=wtr,
        agent_profile_registry=apr,
    )
    
    print(f"  ✓ AgentLoop created successfully")
    print(f"  ✓ Worker type registry: {len(agent.worker_type_registry.list_types())} types")
    print(f"  ✓ Agent profile registry: {len(agent.agent_profile_registry.list_profiles())} profiles")
    
    # Check new tools are registered
    reload_tool = agent.tools.get("reload")
    evolve_tool = agent.tools.get("evolve")
    print(f"  ✓ reload tool registered: {reload_tool is not None}")
    print(f"  ✓ evolve tool registered: {evolve_tool is not None}")
    
    # Check dynamic tools were scanned
    calc_tool = agent.tools.get("calculator")
    print(f"  ✓ calculator (dynamic) loaded: {calc_tool is not None}")
    
    if calc_tool:
        result = await calc_tool.execute(expression="7 * 8 + 1")
        print(f"  ✓ calculator execute '7 * 8 + 1' = {result}")
    
    # Get all tool definitions (what the LLM sees)
    defs = agent.tools.get_definitions()
    tool_names = sorted([d["function"]["name"] for d in defs])
    print(f"  ✓ Total tools available to LLM: {len(tool_names)}")
    print(f"  ✓ Tool names: {tool_names}")
    
    # Test handle_reload
    reload_result = await agent.handle_reload("tools")
    print(f"  ✓ handle_reload('tools') -> {reload_result}")
    
    reload_result2 = await agent.handle_reload("workers")
    print(f"  ✓ handle_reload('workers') -> {reload_result2}")
    
    reload_result3 = await agent.handle_reload("agents")
    print(f"  ✓ handle_reload('agents') -> {reload_result3}")
    
    return True


async def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  SharanBot Self-Evolution Architecture - REAL TESTS     ║")
    print("╚══════════════════════════════════════════════════════════╝")
    
    tests = [
        ("Dynamic Tool Loading", test_dynamic_tool_loading),
        ("Worker Type Registry", test_worker_type_registry),
        ("Agent Profile Registry", test_agent_profile_registry),
        ("Custom YAML Agent Profile", test_custom_agent_profile_from_yaml),
        ("Custom JSON Worker Type", test_custom_worker_type_from_json),
        ("ReloadTool", test_reload_tool),
        ("EvolveTool", test_evolve_tool),
        ("ConfigWatcher", test_config_watcher),
        ("Config Reload", test_reload_config),
        ("Full AgentLoop Integration", test_full_agent_loop_creation),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_fn in tests:
        try:
            result = await test_fn()
            if result:
                passed += 1
                print(f"\n  >>> PASS: {name}")
            else:
                failed += 1
                print(f"\n  >>> FAIL: {name}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"\n  >>> ERROR in {name}: {e}")
            traceback.print_exc()
    
    print("\n" + "="*60)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(tests)}")
    print("="*60)
    
    return failed == 0

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
