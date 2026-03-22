"""One-shot script to create a dynamic calculator tool plugin."""
import os
from pathlib import Path

ws = Path(os.path.expanduser("~/.sharanbot/workspace"))
tool_path = ws / "plugins" / "tools" / "calculator.py"
tool_path.write_text('''
from sharanbot.agent.tools.base import Tool
from typing import Any

class CalculatorTool(Tool):
    """A calculator tool created dynamically."""

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "Perform arithmetic calculations. Supports +, -, *, /, ** (power), % (modulo)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to evaluate, e.g. 2+3*4",
                },
            },
            "required": ["expression"],
        }

    async def execute(self, expression: str, **kwargs: Any) -> str:
        import ast
        import operator
        # Safe math evaluation using AST
        ops = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Pow: operator.pow, ast.Mod: operator.mod,
            ast.USub: operator.neg,
        }
        def _eval(node):
            if isinstance(node, ast.Constant):
                return node.value
            elif isinstance(node, ast.BinOp):
                return ops[type(node.op)](_eval(node.left), _eval(node.right))
            elif isinstance(node, ast.UnaryOp):
                return ops[type(node.op)](_eval(node.operand))
            else:
                raise ValueError(f"Unsupported: {type(node).__name__}")
        try:
            tree = ast.parse(expression, mode="eval")
            result = _eval(tree.body)
            return f"{expression} = {result}"
        except Exception as e:
            return f"Error: {e}"
''', encoding="utf-8")
print(f"Tool written to: {tool_path}")
print(f"File size: {tool_path.stat().st_size} bytes")
