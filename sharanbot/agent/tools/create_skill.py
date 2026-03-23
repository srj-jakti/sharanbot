"""CreateSkillTool — scaffold modular skill packages.

A *skill* in SharanBot is a self-contained capability described by a
SKILL.md manifest (with YAML front-matter) and optional scripts/ directory.
Skills can be shared, versioned, and loaded dynamically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


_SKILL_MD_TEMPLATE = """\
---
name: {name}
version: "0.1.0"
description: "{description}"
author: "SharanBot"
triggers:
  - "{slug}"
inputs:
  - name: query
    type: string
    required: true
    description: "Input for the skill"
---

# {name}

{description}

## Usage

Invoke this skill by saying: `@{slug} <query>`

## Implementation

Add your skill logic in the `scripts/` directory or directly below.

```python
# Example skill handler
def run(query: str) -> str:
    return f"Skill {name} received: {{query}}"
```
"""


class CreateSkillTool(Tool):
    name = "create_skill"
    description = (
        "Create a new modular skill package with a SKILL.md manifest "
        "and optional scripts directory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "Name for the skill (e.g. 'web_research')",
            },
            "description": {
                "type": "string",
                "description": "What the skill does",
            },
        },
        "required": ["skill_name"],
    }

    async def execute(self, **kwargs: Any) -> str:
        name: str = kwargs["skill_name"]
        desc: str = kwargs.get("description", f"A {name} skill for SharanBot")

        slug = name.lower().replace(" ", "_").replace("-", "_")
        skill_dir = Path("sharanbot/skills") / slug
        skill_dir.mkdir(parents=True, exist_ok=True)

        # SKILL.md manifest
        manifest = _SKILL_MD_TEMPLATE.format(name=name, slug=slug, description=desc)
        (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")

        # scripts directory
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "__init__.py").write_text("", encoding="utf-8")

        logger.info("Created skill: {}", skill_dir)
        return f"✅ Created skill `{slug}` at `{skill_dir}/`"
