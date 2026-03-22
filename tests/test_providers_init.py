"""Tests for lazy provider exports from sharanbot.providers."""

from __future__ import annotations

import importlib
import sys


def test_importing_providers_package_is_lazy(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "sharanbot.providers", raising=False)
    monkeypatch.delitem(sys.modules, "sharanbot.providers.litellm_provider", raising=False)
    monkeypatch.delitem(sys.modules, "sharanbot.providers.openai_codex_provider", raising=False)
    monkeypatch.delitem(sys.modules, "sharanbot.providers.azure_openai_provider", raising=False)

    providers = importlib.import_module("sharanbot.providers")

    assert "sharanbot.providers.litellm_provider" not in sys.modules
    assert "sharanbot.providers.openai_codex_provider" not in sys.modules
    assert "sharanbot.providers.azure_openai_provider" not in sys.modules
    assert providers.__all__ == [
        "LLMProvider",
        "LLMResponse",
        "LiteLLMProvider",
        "OpenAICodexProvider",
        "AzureOpenAIProvider",
    ]


def test_explicit_provider_import_still_works(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "sharanbot.providers", raising=False)
    monkeypatch.delitem(sys.modules, "sharanbot.providers.litellm_provider", raising=False)

    namespace: dict[str, object] = {}
    exec("from sharanbot.providers import LiteLLMProvider", namespace)

    assert namespace["LiteLLMProvider"].__name__ == "LiteLLMProvider"
    assert "sharanbot.providers.litellm_provider" in sys.modules
