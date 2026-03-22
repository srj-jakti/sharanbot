"""Send challenge directly to SharanBot's agent loop and capture output."""

import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, ".")

CHALLENGE = """Build me a 3D car racing game using React Native + Expo with the following requirements:

1. **Research Phase**: Research the best approach for 3D game development in React Native/Expo (Three.js, expo-three, react-three-fiber, etc.)

2. **Development Phase**:
   - 3D car model with basic physics (acceleration, braking, steering)
   - A race track with turns, scenery (trees, barriers, sky)
   - Particle effects for tire smoke/dust when drifting
   - Speed indicator HUD
   - Lap counter and timer
   - At least 2 different camera angles (behind car, top-down)
   - Touch controls (left/right steering, accelerate/brake buttons)
   - Start screen with "Start Race" button
   - Game over screen with lap times

3. **Bundle Phase**:
   - Create a complete Expo project that can be built
   - Provide the command to start the Expo dev server
   - Generate an Expo QR code so I can test on my phone

Build the COMPLETE project with all files. Use your team orchestration to assign specialists for different parts. Start working now - research, plan, code, and deliver."""


async def run_challenge():
    from sharanbot.agent.loop import AgentLoop
    from sharanbot.bus.queue import MessageBus
    from sharanbot.config.loader import load_config
    from sharanbot.config.paths import get_config_path
    from sharanbot.session.manager import SessionManager
    from sharanbot.agent.profiles import AgentProfileRegistry
    from sharanbot.agent.worker_registry import WorkerTypeRegistry

    print("=" * 70)
    print("SHARANBOT REAL-LIFE CHALLENGE - DIRECT MODE")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 70)

    # Load config
    config_path = get_config_path()
    config = load_config(config_path)
    print(f"[SETUP] Config loaded from {config_path}")

    # Create components
    bus = MessageBus()
    from sharanbot.cli.commands import _make_provider
    provider_cfg, provider_name = config._match_provider()
    provider = _make_provider(config)

    session_mgr = SessionManager(config.workspace_path)
    plugins_dir = config.workspace_path / "plugins"
    profile_registry = AgentProfileRegistry(plugins_dir=plugins_dir / "agents")
    profile_registry.scan_plugins()
    worker_registry = WorkerTypeRegistry(plugins_dir=plugins_dir / "workers")
    worker_registry.scan_plugins()

    print(f"[SETUP] Provider: {provider_name}, Model: {config.agents.defaults.model}")
    print(f"[SETUP] Profiles: {len(profile_registry.list_profiles())} available")

    # Create worker manager if enabled
    worker_mgr = None
    if config.worker.enable:
        from sharanbot.agent.worker import WorkerManager
        worker_mgr = WorkerManager(
            workspace=config.workspace_path,
            api_key=provider_cfg.api_key if provider_cfg else "",
            api_base=provider_cfg.api_base if provider_cfg else None,
            model=config.agents.defaults.model,
            extra_headers=dict(provider_cfg.extra_headers) if provider_cfg and provider_cfg.extra_headers else None,
            mcp_servers=config.tools.mcp_servers,
            worker_type_registry=worker_registry,
        )
        print(f"[SETUP] WorkerManager enabled")

    # Create agent
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_mgr,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        worker_manager=worker_mgr,
        worker_type_registry=worker_registry,
        agent_profile_registry=profile_registry,
    )

    print(f"[SETUP] Agent created with {config.agents.defaults.max_tool_iterations} max iterations")
    print(f"[SETUP] Context window: {config.agents.defaults.context_window_tokens} tokens")

    # Track progress
    progress_msgs = []

    async def on_progress(content: str, **kwargs):
        ts = time.time()
        safe = content[:200].encode("ascii", errors="replace").decode()
        hint = " [TOOL]" if kwargs.get("tool_hint") else ""
        print(f"  [PROGRESS{hint}] ({len(content)} chars) {safe}")
        progress_msgs.append({"time": ts, "content": content, **kwargs})

    # Send challenge
    print(f"\n[CHALLENGE] Sending 3D car racing game challenge...")
    print(f"[CHALLENGE] Message length: {len(CHALLENGE)} chars")
    start_time = time.time()

    try:
        response = await agent.process_direct(
            CHALLENGE,
            session_key="challenge:car_racing",
            channel="cli",
            chat_id="direct",
            on_progress=on_progress,
        )
        elapsed = time.time() - start_time
        print(f"\n{'=' * 70}")
        print(f"[DONE] Response received in {elapsed:.1f}s")
        print(f"[DONE] Response length: {len(response.content) if response else 0} chars")
        print(f"[DONE] Progress messages: {len(progress_msgs)}")
        print(f"{'=' * 70}")

        if response and response.content:
            # Save full response
            output_file = Path("challenge_full_output.txt")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"SHARANBOT CHALLENGE - 3D Car Racing Game\n")
                f.write(f"Time: {datetime.now().isoformat()}\n")
                f.write(f"Elapsed: {elapsed:.1f}s\n")
                f.write(f"Progress messages: {len(progress_msgs)}\n")
                f.write(f"{'=' * 70}\n\n")
                f.write("RESPONSE:\n")
                f.write(response.content)
                f.write(f"\n\n{'=' * 70}\n")
                f.write(f"\nPROGRESS LOG:\n")
                for i, p in enumerate(progress_msgs, 1):
                    f.write(f"\n--- Progress {i} ---\n")
                    f.write(p["content"][:500])
                    f.write("\n")
            print(f"\nFull output saved to {output_file}")

            # Print preview
            preview = response.content[:2000].encode("ascii", errors="replace").decode()
            print(f"\n[PREVIEW]\n{preview}")
            if len(response.content) > 2000:
                print(f"\n... ({len(response.content) - 2000} more chars)")
        else:
            print("[WARN] Empty response")

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n[ERROR] Challenge failed after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await agent.close_mcp()
        print("\n[CLEANUP] MCP connections closed")


if __name__ == "__main__":
    asyncio.run(run_challenge())
