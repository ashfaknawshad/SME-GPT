"""Agentic conversational query engine -- Phase 1 entry point.

Generalizes the PAL neuro-symbolic architecture (docs/components/component-3.md)
from a single rigid plan-execute-answer cycle into a multi-turn, tool-calling
conversation: the LLM still only *plans* by calling deterministic tools
(agent/tools.py); it never computes a number itself (agent/guard.py is the
last-line-of-defense check). Conversation memory persists per `thread_id`
(agent/memory.py). Tenant isolation is preserved exactly like the legacy/PAL
engines: user_id is bound into the tools server-side, never an LLM-suppliable
argument (CLAUDE.md "Architecture Invariants").

Stage A additions (docs/phase3-retirement-plan.md): each turn also returns a
`trace` -- the tool calls the agent actually made (name, args, result summary),
which IS the derivation trace for the UI: exactly what was computed, with what
filters, from how many rows. The turn (messages + evidence + trace) is
persisted to the chat_thread/chat_message registry (agent/threads.py) for
ChatGPT-style thread listing and replay.

Runs alongside the existing /ask-query (pal_qa.answer_financial_question)
without touching it -- see app.py's AGENT_QUERY_ENGINE_ENABLED flag.
"""
from __future__ import annotations

import json
import threading
import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.graph import build_agent_graph
from agent.guard import content_text, is_blocked_request
from agent.language import REQUEST_REFUSED, detect_reply_language
from agent.memory import get_checkpointer
from agent.threads import record_turn, rename_thread
from agent.tools import build_tools

_TITLE_SYSTEM = (
    "You write a very short conversation title (3-6 words, no quotes, no trailing "
    "punctuation) summarizing what the user is asking about. Reply with the title only."
)


def generate_thread_title(question: str, answer: str) -> str | None:
    """A concise LLM-generated title for a new conversation's sidebar entry.

    Best-effort: returns None on any failure so the caller falls back to the
    truncated first question. Routed through the query LLM (Gemini/DeepSeek).
    """
    try:
        from llm_client import call_llm
        raw = call_llm(
            f"User question: {question}\nAssistant answer: {answer[:400]}\n\nTitle:",
            system=_TITLE_SYSTEM,
        )
        title = " ".join(str(raw).strip().strip('"').split())
        return title[:60] or None
    except Exception:
        return None

# Keep per-tool-call result snapshots in the trace bounded -- the full evidence
# already travels separately; the trace is a readable derivation summary.
_TRACE_RESULT_MAX_CHARS = 600


def _summarize_result(content) -> str:
    if isinstance(content, (dict, list)):
        text = json.dumps(content, ensure_ascii=False, default=str)
    else:
        text = str(content)
    return text if len(text) <= _TRACE_RESULT_MAX_CHARS else text[:_TRACE_RESULT_MAX_CHARS] + "…"


def extract_turn_trace(messages: list) -> list[dict]:
    """Derivation trace for the CURRENT turn: every tool call the agent made
    since the latest user message, paired with the deterministic result it got
    back. Pure function over the graph's message list (hermetically testable)."""
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0
    )
    turn = messages[last_human_idx:]

    results_by_call_id: dict[str, ToolMessage] = {
        m.tool_call_id: m for m in turn
        if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", None)
    }

    trace: list[dict] = []
    step = 0
    for m in turn:
        if not (isinstance(m, AIMessage) and getattr(m, "tool_calls", None)):
            continue
        for call in m.tool_calls:
            step += 1
            result_msg = results_by_call_id.get(call.get("id"))
            trace.append({
                "step": step,
                "tool": call.get("name"),
                "args": call.get("args") or {},
                "result": _summarize_result(result_msg.content) if result_msg is not None else None,
            })
    return trace


def chat(question: str, user_id: str, company_name: str, thread_id: str | None = None,
         document_id: str | None = None) -> dict:
    """Runs one turn of the conversational agent.

    document_id (Stage C): when set, the conversation is scoped to that document
    -- the agent resolves "this document" to it, and the thread is tagged so the
    UI can keep showing its image. Bound into the thread at creation; carried on
    every turn so the system prompt stays document-aware across the conversation.

    Returns a dict with `success`, `answer`, `evidence`, `trace`, `thread_id`.
    Raises llm_client.LLMUnavailableError if no cloud LLM provider is
    configured (there is no local fallback -- see llm_client.py's PRIVACY
    TRADE-OFF note).
    """
    is_new_thread = thread_id is None
    thread_id = thread_id or f"{user_id}:{uuid.uuid4().hex}"

    # Refused before any LLM call, so no wording can talk the model round.
    if is_blocked_request(question):
        refusal = REQUEST_REFUSED[detect_reply_language(question)]
        record_turn(
            thread_id=thread_id, user_id=user_id, company_name=company_name,
            question=question, answer=refusal, evidence=[], trace=[],
            document_id=document_id,
        )
        return {"success": True, "answer": refusal, "evidence": [], "trace": [], "thread_id": thread_id}

    tools, evidence = build_tools(user_id=user_id, company_name=company_name)
    checkpointer = get_checkpointer()
    graph = build_agent_graph(tools, checkpointer, company_name=company_name, document_id=document_id)

    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"messages": [HumanMessage(content=question)]}, config=config)

    final = result["messages"][-1]
    answer_text = content_text(getattr(final, "content", ""))
    trace = extract_turn_trace(result["messages"])

    # Best-effort UI history (never fails the request) -- see agent/threads.py.
    record_turn(
        thread_id=thread_id, user_id=user_id, company_name=company_name,
        question=question, answer=answer_text, evidence=evidence, trace=trace,
        document_id=document_id,
    )

    # On a brand-new thread, upgrade the auto-title (truncated question) to a
    # concise LLM-generated one. Done in the background so it never adds latency
    # to the user's answer; the sidebar picks it up on its next refresh.
    if is_new_thread:
        def _title_worker():
            title = generate_thread_title(question, answer_text)
            if title:
                rename_thread(thread_id, user_id, title)
        threading.Thread(target=_title_worker, daemon=True).start()

    return {
        "success": True,
        "answer": answer_text,
        "evidence": evidence,
        "trace": trace,
        "thread_id": thread_id,
    }
