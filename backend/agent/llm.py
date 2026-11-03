"""LangChain chat-model factory for the agentic query engine (Phase 1).

Reuses llm_client.py's provider selection (Gemini if GEMINI_API_KEY is set,
otherwise DeepSeek) so there is exactly one source of truth for which cloud
LLM is active -- this module only adapts that choice into a LangChain
BaseChatModel the LangGraph agent can bind tools to. pal_planner / pal_answer
/ ai_helper (the existing PAL + legacy query engines behind /ask-query) are
untouched and keep calling llm_client.call_llm directly.

There is no local (Ollama) fallback here either -- see the PRIVACY TRADE-OFF
note atop llm_client.py.
"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

import llm_client as _llm_client
from llm_client import LLMUnavailableError


def _gemini_selected() -> bool:
    return _llm_client.QUERY_PROVIDER == "gemini" and bool(_llm_client.GEMINI_API_KEY)


def _deepseek_model(temperature: float) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=_llm_client.DEEPSEEK_BASE_URL,
        api_key=_llm_client.DEEPSEEK_API_KEY,
        model=_llm_client.DEEPSEEK_MODEL,
        temperature=temperature,
    )


def get_chat_model(temperature: float = 0.0) -> BaseChatModel:
    """Returns a tool-callable chat model for the active query provider.

    Raises LLMUnavailableError if neither GEMINI_API_KEY nor DEEPSEEK_API_KEY
    is configured.
    """
    if _gemini_selected():
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:
            if not _llm_client.DEEPSEEK_API_KEY:
                raise LLMUnavailableError(
                    "GEMINI_API_KEY is set but langchain-google-genai is not installed "
                    "(pip install langchain-google-genai) and no DEEPSEEK_API_KEY is set."
                ) from exc
            print("[AGENT] langchain-google-genai missing; using DeepSeek.", flush=True)
        else:
            return ChatGoogleGenerativeAI(
                model=_llm_client.GEMINI_TUNED_QUERY_MODEL or _llm_client.GEMINI_MODEL,
                google_api_key=_llm_client.GEMINI_API_KEY,
                temperature=temperature,
            )

    if _llm_client.DEEPSEEK_API_KEY:
        return _deepseek_model(temperature)

    raise LLMUnavailableError(
        "No LLM provider is available for the agent query engine. Set "
        "GEMINI_API_KEY or DEEPSEEK_API_KEY (local Ollama inference has been removed)."
    )


def bind_tools_with_fallback(model: BaseChatModel, tools: list, temperature: float = 0.0):
    """Bind `tools` to `model`, falling back to DeepSeek on any error when the
    primary is Gemini and a DeepSeek key exists — the same Gemini-then-DeepSeek
    order llm_client.call_llm uses, so one provider being down or rate-limited
    doesn't take the assistant down with it."""
    bound = model.bind_tools(tools)
    if not (_gemini_selected() and _llm_client.DEEPSEEK_API_KEY):
        return bound
    if type(model).__name__ != "ChatGoogleGenerativeAI":
        return bound  # already DeepSeek (e.g. the Gemini package is missing)
    return bound.with_fallbacks([_deepseek_model(temperature).bind_tools(tools)])
