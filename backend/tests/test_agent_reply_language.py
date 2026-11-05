"""Chat replies in the language of the latest question (agent/language.py).

English -> English, Sinhala script -> Sinhala, Singlish (Sinhala typed in
English letters) -> Singlish, independent of the UI language toggle and of the
language used earlier in the conversation. Hermetic: fake model, no LLM calls.
"""
from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

import agent.graph as agent_graph
from agent.language import (
    GUARD_FALLBACK,
    GUARD_FALLBACK_NO_DATA,
    detect_reply_language,
    reply_language_instruction,
)


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "How much do customers still owe me?",
    "How much have I paid City Transport Services?",
    "What is Sanduni Hardware's total profit if I sell the moon?",
    "If I add 15% VAT to the Kandy Paint House invoice, what is the total?",
    "Show my outstanding payables for this month",
    "Do any invoice prices differ from the order?",
    "Help me plan next month's budget",
    "hi",
    "Which supplier did I pay the most in October?",
    "List all delivery notes from Ruhunu Paint",
    "What is the API status of invoice IN27?",
    "Can you give me one summary of the data?",
])
def test_english_questions_stay_english(text):
    assert detect_reply_language(text) == "en"


@pytest.mark.parametrize("text", [
    "මට ගනුදෙනුකරුවන්ගෙන් තව කීයක් ලැබිය යුතුද?",
    "City Transport Services එකට මම කීයක් ගෙව්වාද?",
    "මගේ invoice total එක කීයද?",
    "ආයුබෝවන්",
    "මේ මාසයේ ලාභය කීයද",
])
def test_sinhala_script_is_sinhala(text):
    assert detect_reply_language(text) == "si"


@pytest.mark.parametrize("text", [
    "mata customersla gen thawa labenna thiyena salli kiyada?",
    "City Transport Services ekata mama gewapu ganan kiyada",
    "me masa labaya kiyada",
    "mage invoice eka balanna puluwanda?",
    "kohomada",
    "supplierslata gewanna thiyena bills monawada?",
    "mata budget eka plan karanna udaw karanna",
    "ada wiyadam kiyada",
])
def test_singlish_is_singlish(text):
    assert detect_reply_language(text) == "singlish"


def test_empty_text_defaults_to_english():
    assert detect_reply_language("") == "en"
    assert detect_reply_language("12345 ???") == "en"


def test_instructions_name_the_target_language():
    assert "English" in reply_language_instruction("en")
    assert "Sinhala script" in reply_language_instruction("si") or "සිංහල" in reply_language_instruction("si")
    singlish = reply_language_instruction("singlish")
    assert "Latin" in singlish and "do NOT use Sinhala script" in singlish
    # unknown codes fall back to English
    assert reply_language_instruction("xx") == reply_language_instruction("en")


# ---------------------------------------------------------------------------
# graph: the per-turn instruction reaches the model, and follows each turn
# ---------------------------------------------------------------------------

class RecordingModel(FakeMessagesListChatModel):
    systems: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, *args, **kwargs):
        RecordingModel.systems.append(str(messages[0].content))
        return super().invoke(messages, *args, **kwargs)


def _graph(monkeypatch, replies):
    RecordingModel.systems = []
    model = RecordingModel(responses=[AIMessage(content=r) for r in replies])
    monkeypatch.setattr(agent_graph, "get_chat_model", lambda temperature=0.0: model)
    return agent_graph.build_agent_graph([], MemorySaver(), company_name="AIESEC")


def test_language_follows_each_turn_not_the_thread(monkeypatch):
    graph = _graph(monkeypatch, ["Hello!", "ආයුබෝවන්!", "Hari, mama udaw karannam.", "Sure."])
    cfg = {"configurable": {"thread_id": "t-lang"}}

    for q in ["hi there", "ආයුබෝවන්", "mata udaw karanna puluwanda?", "thanks, that's all"]:
        graph.invoke({"messages": [HumanMessage(content=q)]}, config=cfg)

    tags = [s.split("REPLY LANGUAGE FOR THIS TURN:")[1].split(".")[0].strip() for s in RecordingModel.systems]
    assert tags[0].startswith("English")
    assert tags[1].startswith("Sinhala")
    assert tags[2].startswith("Singlish")
    assert tags[3].startswith("English")   # switched back -- not stuck on the thread's history


def test_guard_fallback_uses_question_language(monkeypatch):
    # An answer quoting a figure no tool produced is replaced by the guard;
    # the replacement must be in the language the user asked in.
    graph = _graph(monkeypatch, ["ඔබට LKR 999,999 ලැබිය යුතුයි."])
    out = graph.invoke({"messages": [HumanMessage(content="මට කීයක් ලැබිය යුතුද?")]},
                       config={"configurable": {"thread_id": "t-guard"}})
    assert out["messages"][-1].content == GUARD_FALLBACK_NO_DATA["si"]


def test_guard_fallbacks_exist_for_every_language():
    for lang in ("en", "si", "singlish"):
        assert GUARD_FALLBACK[lang] and GUARD_FALLBACK_NO_DATA[lang]
