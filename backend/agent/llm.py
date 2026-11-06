"""LangChain chat-model factory for the agentic query engine (Phase 1).

The assistant runs on DeepSeek only. This module adapts llm_client.py's DeepSeek
settings into a LangChain BaseChatModel the LangGraph agent can bind tools to.
pal_planner / pal_answer / ai_helper (the existing PAL + legacy query engines
behind /ask-query) are untouched and keep calling llm_client.call_llm directly.

There is no local (Ollama) fallback here either -- see the PRIVACY TRADE-OFF
note atop llm_client.py.
"""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

import llm_client as _llm_client
from llm_client import LLMUnavailableError

_AGENT_TIMEOUT_SECS = 60
_AGENT_MAX_RETRIES = 2


def get_chat_model(temperature: float = 0.0) -> BaseChatModel:
    """Returns a tool-callable DeepSeek chat model.

    Raises LLMUnavailableError if DEEPSEEK_API_KEY is not configured.
    """
    if not _llm_client.DEEPSEEK_API_KEY:
        raise LLMUnavailableError(
            "No LLM provider is available for the agent query engine. Set "
            "DEEPSEEK_API_KEY (local Ollama inference has been removed)."
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=_llm_client.DEEPSEEK_BASE_URL,
        api_key=_llm_client.DEEPSEEK_API_KEY,
        model=_llm_client.DEEPSEEK_MODEL,
        temperature=temperature,
        timeout=_AGENT_TIMEOUT_SECS,
        max_retries=_AGENT_MAX_RETRIES,
        # Thinking mode is off: tool rounds would otherwise have to echo back
        # reasoning_content, which LangChain drops, and answers take longer.
        extra_body={"thinking": {"type": "disabled"}},
    )
