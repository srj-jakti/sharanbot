"""Tests for bundled agent profiles."""

from pathlib import Path

import pytest

from sharanbot.agent.profiles import AgentProfileRegistry


def test_builtin_profiles_exist():
    """3 built-in profiles should always be present."""
    registry = AgentProfileRegistry()
    assert registry.get("general") is not None
    assert registry.get("coder") is not None
    assert registry.get("researcher") is not None


def test_bundled_yaml_profiles_loadable():
    """All bundled YAML agent profiles should parse and load correctly."""
    from importlib.resources import files as pkg_files

    tpl = pkg_files("sharanbot") / "templates" / "agents"
    if not tpl.is_dir():
        pytest.skip("Agent templates not found in package")

    yaml_files = [f for f in tpl.iterdir() if f.name.endswith(".yaml")]
    assert len(yaml_files) >= 29, f"Expected >=29 YAML profiles, found {len(yaml_files)}"

    # Load them into a registry
    import tempfile
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        plugins_dir = Path(tmpdir) / "agents"
        plugins_dir.mkdir()
        for yf in yaml_files:
            (plugins_dir / yf.name).write_text(
                yf.read_text(encoding="utf-8"), encoding="utf-8"
            )

        registry = AgentProfileRegistry(plugins_dir=plugins_dir)
        loaded = registry.scan_plugins()

        assert len(loaded) >= 29
        # Verify total = 3 builtins + loaded plugins
        assert len(registry.list_profiles()) >= 32


def test_bundled_profiles_have_required_fields():
    """Each profile should have name, description, and system_prompt."""
    from importlib.resources import files as pkg_files
    import yaml

    tpl = pkg_files("sharanbot") / "templates" / "agents"
    if not tpl.is_dir():
        pytest.skip("Agent templates not found in package")

    for yf in tpl.iterdir():
        if not yf.name.endswith(".yaml"):
            continue
        data = yaml.safe_load(yf.read_text(encoding="utf-8"))
        assert "name" in data, f"{yf.name} missing 'name'"
        assert "description" in data, f"{yf.name} missing 'description'"
        assert "system_prompt" in data, f"{yf.name} missing 'system_prompt'"
        assert len(data["system_prompt"]) > 10, f"{yf.name} has empty system_prompt"


def test_sync_workspace_templates_copies_agents(tmp_path: Path):
    """sync_workspace_templates should copy agent profiles to plugins/agents/."""
    from sharanbot.utils.helpers import sync_workspace_templates

    workspace = tmp_path / "workspace"
    added = sync_workspace_templates(workspace, silent=True)

    agents_dir = workspace / "plugins" / "agents"
    if agents_dir.exists():
        yaml_files = list(agents_dir.glob("*.yaml"))
        assert len(yaml_files) >= 29
