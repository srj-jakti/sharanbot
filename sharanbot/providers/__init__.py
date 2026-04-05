"""LLM provider abstraction module."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from sharanbot.providers.base import LLMProvider, LLMResponse

__all__ = ["LLMProvider", "LLMResponse", "LiteLLMProvider", "OpenAICodexProvider", "AzureOpenAIProvider"]

_LAZY_IMPORTS = {
    "LiteLLMProvider": ".litellm_provider",
    "OpenAICodexProvider": ".openai_codex_provider",
    "AzureOpenAIProvider": ".azure_openai_provider",
}

if TYPE_CHECKING:
    from sharanbot.providers.azure_openai_provider import AzureOpenAIProvider
    from sharanbot.providers.litellm_provider import LiteLLMProvider
    from sharanbot.providers.openai_codex_provider import OpenAICodexProvider


def __getattr__(name: str):
    """Lazily expose provider implementations without importing all backends up front."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is not None:
        module = import_module(module_name, __name__)
        return getattr(module, name)
    # Allow access to submodules (e.g. sharanbot.providers.base) for monkeypatching
    try:
        return import_module(f".{name}", __name__)
    except ImportError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
