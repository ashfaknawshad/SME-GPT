"""Component 3 (generalized) -- conversational agent graph (Phase 1).

A single ReAct tool-calling loop built on langgraph.graph.StateGraph directly
(not the deprecated langgraph.prebuilt.create_react_agent) so a grounding
guard node can run right before the final answer leaves the graph. The LLM
only ever *plans* by calling deterministic tools (agent/tools.py, backed by
pal_executor/pal_validator); it never computes a number itself. The guard
node (agent/guard.py) is the last line of defense: if the final answer states
a monetary figure no tool call in this turn actually produced, it is replaced
with a safe, tool-grounded fallback (docs/components/component-3.md "Why").

Agents-only-if-needed: this is a single agent, not a multi-agent system --
there is no evidence a second agent is needed for Phase 1's scope.
"""
from __future__ import annotations

from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from agent.guard import answer_is_grounded, collect_allowed_numbers
from agent.llm import get_chat_model

_SYSTEM_PROMPT = """You are the financial advisory hub for Sri Lankan SMEs, speaking with the \
owner of {company_name} -- an ongoing planning partner, not just a lookup tool. Answer in \
whichever language (English, Sinhala, or a natural mix) the user writes in.

You have tools to search documents and compute financial totals -- use them for any question \
that needs real data. NEVER state a specific monetary figure, count, or total unless a tool \
call returned it earlier in this conversation. If you don't have a tool result for a number, \
call the right tool first instead of guessing.

TODAY'S DATE is {today}. Resolve relative periods ("this month", "last month", "this year") \
against this date yourself before calling a tool with a doc_date filter.

ADVICE AND PLANNING: when asked for advice or ideas ("how do I reduce receivables?", "how can I \
manage my budget?"), call aggregate_financials / search_documents first to ground your advice in \
the real numbers, then give concrete, prioritized, actionable steps -- name actual suppliers or \
amounts where it helps, not generic filler.

When asked to help PLAN a period ("help me plan next month", "help me budget"), check first \
whether you already have what you need (real data via tools, or something the user told you \
earlier in this conversation). If a key input is still missing -- e.g. their expected income or \
expenses for that period -- do NOT guess: ask ONE focused clarifying question and stop there for \
this turn (no tool call needed for a plain question). Once they answer in a later message, \
combine that answer with the real financial data to build a concrete, numbered plan.

If the user greets you or makes small talk, respond briefly and naturally without calling any \
tool -- only use tools for questions that need real financial data.

Keep answers concise and business-friendly. Mention the currency when quoting an amount."""

# Appended to the system prompt when the conversation was opened from a specific
# document (Stage C -- "Ask about this document"). Resolves deictic references
# ("this invoice", "this document") to a concrete id the tools can look up.
_DOC_CONTEXT = """

CURRENT DOCUMENT: the user opened this conversation from document {document_id} and is looking \
at it right now. When they say "this document", "this invoice", "this PO", "this receipt", or \
similar, they mean {document_id} -- call get_document_status("{document_id}") to get its details \
before answering about it."""

# Bounds the ReAct loop per-turn so a confused agent can't loop forever.
MAX_TOOL_ITERATIONS = 6


def _current_turn(messages: list) -> list:
    """Messages since (and including) the most recent HumanMessage."""
    last_human_idx = max(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0
    )
    return messages[last_human_idx:]


def _should_continue(state: MessagesState) -> str:
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "guard"

    turn = _current_turn(state["messages"])
    rounds = sum(1 for m in turn if getattr(m, "tool_calls", None))
    if rounds > MAX_TOOL_ITERATIONS:
        return "guard"
    return "tools"


def _guard_node(state: MessagesState) -> dict:
    turn = _current_turn(state["messages"])
    final = turn[-1]

    if not isinstance(final, AIMessage) or not final.content:
        return {}

    # Ground against every figure the tools produced across the WHOLE
    # conversation, not just this turn. Restating a total that a tool computed a
    # few turns ago (e.g. "of that LKR 541,750, break it down…") is legitimate
    # continuity, not a fabricated number — grounding only against the current
    # turn's tools was flagging those as unverified and blanking good answers.
    all_msgs = state["messages"]
    tool_outputs = [m.content for m in all_msgs if isinstance(m, ToolMessage)]
    human_texts = [m.content for m in all_msgs if isinstance(m, HumanMessage)]

    allowed = collect_allowed_numbers(tool_outputs, human_texts)
    if answer_is_grounded(final.content, allowed):
        return {}

    # Never dump raw tool JSON into the chat bubble — the exact figures the tools
    # returned are already surfaced in the "How I worked this out" trace and the
    # Sources panel. The bubble stays a clean, honest sentence pointing there.
    fallback = (
        "I pulled the figures but couldn't fully verify the answer I was about to "
        "give, so I'd rather not state a number I'm unsure of. You can see exactly "
        "what my tools returned under “How I worked this out” below — or "
        "ask me to try again."
    ) if tool_outputs else (
        "I couldn't ground that answer in verified data — could you rephrase your question?"
    )
    # Returning a message with the same id REPLACES it in MessagesState's
    # add_messages reducer, so this overwrites the ungrounded answer in place.
    return {"messages": [AIMessage(content=fallback, id=final.id)]}


def build_agent_graph(tools: list, checkpointer, company_name: str, document_id: str | None = None):
    model = get_chat_model().bind_tools(tools)
    prompt = _SYSTEM_PROMPT.format(company_name=company_name, today=date.today().isoformat())
    if document_id:
        prompt += _DOC_CONTEXT.format(document_id=document_id)
    system = SystemMessage(content=prompt)

    def agent_node(state: MessagesState) -> dict:
        response = model.invoke([system] + list(state["messages"]))
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("guard", _guard_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", "guard": "guard"})
    graph.add_edge("tools", "agent")
    graph.add_edge("guard", END)

    return graph.compile(checkpointer=checkpointer)
