"""Local Ollama inference was removed — both tiers route to a cloud provider
(Gemini preferred, DeepSeek default) with no offline fallback.

These tests are hermetic: the provider callers are monkeypatched, so no network
or API key is needed.
"""
from __future__ import annotations

import pytest

import llm_client
from llm_client import LLMUnavailableError


# ── query tier (call_llm) ────────────────────────────────────────────────────

def test_call_llm_uses_deepseek_when_no_gemini(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(llm_client, "QUERY_PROVIDER", "deepseek")
    monkeypatch.setattr(llm_client, "_call_deepseek", lambda *a, **k: "DS_ANSWER")

    assert llm_client.call_llm("hi") == "DS_ANSWER"


def test_call_llm_prefers_gemini_when_configured(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "gm-key")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(llm_client, "QUERY_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_call_gemini", lambda *a, **k: "GEMINI_ANSWER")
    monkeypatch.setattr(llm_client, "_call_deepseek",
                        lambda *a, **k: pytest.fail("DeepSeek should not be called"))

    assert llm_client.call_llm("hi") == "GEMINI_ANSWER"


def test_call_llm_falls_back_to_deepseek_when_gemini_fails(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "gm-key")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(llm_client, "QUERY_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_call_gemini",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gemini down")))
    monkeypatch.setattr(llm_client, "_call_deepseek", lambda *a, **k: "DS_ANSWER")

    assert llm_client.call_llm("hi") == "DS_ANSWER"


def test_call_llm_raises_when_no_provider(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(llm_client, "QUERY_PROVIDER", "deepseek")

    with pytest.raises(LLMUnavailableError):
        llm_client.call_llm("hi")


# ── pipeline tier (call_pipeline_llm) ────────────────────────────────────────

def test_pipeline_raises_when_no_provider(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(llm_client, "PIPELINE_PROVIDER", "deepseek")

    with pytest.raises(LLMUnavailableError):
        # unique prompt to avoid a cache hit from other tests
        llm_client.call_pipeline_llm("unique-prompt-no-provider-xyz")


# ── health ───────────────────────────────────────────────────────────────────

def test_check_deepseek_health_reports_missing_key(monkeypatch):
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "")
    result = llm_client.check_deepseek_health()
    assert result["ok"] is False
    assert "DEEPSEEK_API_KEY" in result["error"]


def test_no_ollama_health_symbol():
    """check_ollama_health must be gone (local inference removed)."""
    assert not hasattr(llm_client, "check_ollama_health")


# ── agent engine (agent/llm.py) ──────────────────────────────────────────────

class ChatGoogleGenerativeAI:
    """Stand-in named like the real Gemini model: answers via `reply` or raises."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error = reply, error

    def bind_tools(self, tools):
        from langchain_core.runnables import RunnableLambda

        def run(_messages):
            if self.error:
                raise self.error
            return self.reply

        return RunnableLambda(run)


def _gemini_and_deepseek(monkeypatch, deepseek_reply="DS_ANSWER"):
    import agent.llm as agent_llm

    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "gm-key")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(llm_client, "QUERY_PROVIDER", "gemini")
    monkeypatch.setattr(agent_llm, "_deepseek_model",
                        lambda temperature: ChatGoogleGenerativeAI(reply=deepseek_reply))
    return agent_llm


def test_agent_falls_back_to_deepseek_when_gemini_fails(monkeypatch):
    agent_llm = _gemini_and_deepseek(monkeypatch)
    gemini = ChatGoogleGenerativeAI(error=RuntimeError("429 quota exceeded"))
    model = agent_llm.bind_tools_with_fallback(gemini, tools=[])
    assert model.invoke(["hi"]) == "DS_ANSWER"


def test_agent_uses_gemini_when_it_works(monkeypatch):
    agent_llm = _gemini_and_deepseek(monkeypatch)
    model = agent_llm.bind_tools_with_fallback(ChatGoogleGenerativeAI(reply="GEMINI_ANSWER"), tools=[])
    assert model.invoke(["hi"]) == "GEMINI_ANSWER"


def test_agent_without_gemini_has_no_fallback_layer(monkeypatch):
    import agent.llm as agent_llm

    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")
    monkeypatch.setattr(llm_client, "QUERY_PROVIDER", "deepseek")
    model = agent_llm.bind_tools_with_fallback(ChatGoogleGenerativeAI(reply="DS_ONLY"), tools=[])
    assert type(model).__name__ == "RunnableLambda"
    assert model.invoke(["hi"]) == "DS_ONLY"
