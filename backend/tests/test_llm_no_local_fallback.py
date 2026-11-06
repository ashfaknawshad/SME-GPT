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


def test_call_llm_prefers_deepseek_when_both_are_configured(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "gm-key")
    monkeypatch.setattr(llm_client, "DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setattr(llm_client, "_call_deepseek", lambda *a, **k: "DEEPSEEK_ANSWER")
    monkeypatch.setattr(llm_client, "_call_gemini",
                        lambda *a, **k: pytest.fail("Gemini should not be called"))

    assert llm_client.call_llm("hi") == "DEEPSEEK_ANSWER"


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
