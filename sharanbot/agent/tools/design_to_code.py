"""DesignToCodeTool — convert screenshots/mockups/descriptions into code.

Uses the LLM's vision capability to analyze an image (screenshot, mockup,
wireframe) and generate HTML/React/Flutter components from it.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from loguru import logger

from sharanbot.agent.tools.base import Tool


class DesignToCodeTool(Tool):
    """Convert visual designs (screenshots, mockups) into code components."""

    def __init__(self, provider=None, model: str | None = None):
        self._provider = provider
        self._model = model

    def set_provider(self, provider, model: str) -> None:
        """Set the LLM provider (called by AgentLoop after init)."""
        self._provider = provider
        self._model = model

    @property
    def name(self) -> str:
        return "design_to_code"

    @property
    def description(self) -> str:
        return (
            "Convert a visual design (screenshot, mockup, wireframe) into code. "
            "Accepts an image file path or a text description of the design. "
            "Generates HTML/React/Flutter/SwiftUI components. "
            "Actions: generate (create code from design), describe (analyze design without generating code)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["generate", "describe"],
                    "description": "Action: generate (create code) or describe (analyze design).",
                },
                "image_path": {
                    "type": "string",
                    "description": "Path to the design image (screenshot, mockup, wireframe).",
                },
                "design_description": {
                    "type": "string",
                    "description": "Text description of the design (used if no image provided, or as additional context).",
                },
                "framework": {
                    "type": "string",
                    "enum": ["html", "react", "nextjs", "vue", "svelte", "flutter", "swiftui", "react-native"],
                    "description": "Target framework for code generation (default: react).",
                },
                "style": {
                    "type": "string",
                    "enum": ["tailwind", "css", "styled-components", "css-modules", "inline"],
                    "description": "Styling approach (default: tailwind).",
                },
                "output_path": {
                    "type": "string",
                    "description": "File path to write the generated code to.",
                },
                "component_name": {
                    "type": "string",
                    "description": "Name for the generated component (default: GeneratedComponent).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, **kwargs: Any) -> str:
        if not self._provider:
            return "Error: design_to_code requires an LLM provider with vision support. Provider not configured."

        image_path = kwargs.get("image_path", "")
        description = kwargs.get("design_description", "")
        framework = kwargs.get("framework", "react")
        style = kwargs.get("style", "tailwind")
        output_path = kwargs.get("output_path", "")
        component_name = kwargs.get("component_name", "GeneratedComponent")

        if not image_path and not description:
            return "Error: provide either image_path or design_description (or both)."

        # Build the prompt
        messages = self._build_messages(
            action=action,
            image_path=image_path,
            description=description,
            framework=framework,
            style=style,
            component_name=component_name,
        )

        if messages is None:
            return f"Error: could not read image at '{image_path}'."

        try:
            response = await self._provider.chat_with_retry(
                messages=messages,
                model=self._model,
            )
            result = response.content or "No output generated."
        except Exception as e:
            return f"Error generating code from design: {e}"

        # Write to file if output_path specified
        if output_path and action == "generate":
            try:
                out = Path(output_path)
                if not out.is_absolute():
                    workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
                    out = workspace / out
                out.parent.mkdir(parents=True, exist_ok=True)
                # Extract code block from response
                code = self._extract_code(result)
                out.write_text(code, encoding="utf-8")
                return f"✅ Generated {framework} component → {out}\n\n{result}"
            except Exception as e:
                return f"Generated code but failed to write to {output_path}: {e}\n\n{result}"

        return result

    def _build_messages(
        self,
        action: str,
        image_path: str,
        description: str,
        framework: str,
        style: str,
        component_name: str,
    ) -> list[dict[str, Any]] | None:
        """Build LLM messages with optional image content."""

        if action == "describe":
            system = (
                "You are a UI/UX analyst. Analyze the provided design and describe:\n"
                "1. Layout structure (header, sidebar, main content, footer)\n"
                "2. Components visible (buttons, forms, cards, tables, etc.)\n"
                "3. Color scheme and typography\n"
                "4. Spacing and alignment patterns\n"
                "5. Interactive elements and their likely behavior\n"
                "Be specific and structured."
            )
        else:
            framework_hints = {
                "react": "React functional component with TypeScript",
                "nextjs": "Next.js App Router component with TypeScript",
                "vue": "Vue 3 Composition API SFC (.vue file)",
                "svelte": "Svelte component (.svelte file)",
                "flutter": "Flutter StatelessWidget in Dart",
                "swiftui": "SwiftUI View struct",
                "react-native": "React Native component with TypeScript",
                "html": "semantic HTML5 with CSS",
            }
            fw_hint = framework_hints.get(framework, framework)
            style_hint = {
                "tailwind": "Tailwind CSS utility classes",
                "css": "separate CSS file",
                "styled-components": "styled-components",
                "css-modules": "CSS Modules",
                "inline": "inline styles",
            }.get(style, style)

            system = (
                f"You are an expert frontend developer. Convert the provided design into a {fw_hint} "
                f"using {style_hint} for styling.\n\n"
                f"Component name: {component_name}\n\n"
                "Rules:\n"
                "1. Match the design as closely as possible\n"
                "2. Use semantic HTML elements\n"
                "3. Make it responsive (mobile-first)\n"
                "4. Include proper TypeScript types where applicable\n"
                "5. Add aria labels for accessibility\n"
                "6. Use placeholder content that matches the design's intent\n"
                "7. Output ONLY the code — no explanations before or after\n"
                "8. Include all necessary imports"
            )

        content_parts: list[dict[str, Any]] = []

        # Add image if provided
        if image_path:
            img_data = self._load_image(image_path)
            if img_data is None:
                return None
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": img_data},
            })

        # Add text description
        text = description or "Convert this design into code."
        if image_path and description:
            text = f"Additional context: {description}"
        content_parts.append({"type": "text", "text": text})

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": content_parts},
        ]

    @staticmethod
    def _load_image(path: str) -> str | None:
        """Load an image file and return as data URL."""
        p = Path(path)
        if not p.is_absolute():
            workspace = Path(os.environ.get("SHARANBOT_WORKSPACE", "."))
            p = workspace / p

        if not p.exists():
            return None

        suffix = p.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".bmp": "image/bmp",
        }.get(suffix, "image/png")

        try:
            data = base64.b64encode(p.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{data}"
        except Exception as e:
            logger.warning("Failed to load image {}: {}", path, e)
            return None

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extract code from markdown code blocks, or return raw text."""
        import re
        match = re.search(r"```(?:\w+)?\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()
