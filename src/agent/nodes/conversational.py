"""
conversational_node.py
======================
Handles casual greetings, chit-chat, and simple identity questions WITHOUT
triggering any search tool (web search, Gmail, KB, etc.).

This node is reached when the ClassifierController returns:
    request_type = "conversational"

It passes the user's message directly to the LLM with a persona prompt and
returns a short, natural reply.
"""

import logging

from agent.deps import AgentDeps
from agent.state import AgentState
from helpers.prompts import render

logger = logging.getLogger("uvicorn.error")

# ---------------------------------------------------------------------------
# Friendly hard-coded fallbacks (used when LLM is unavailable)
# ---------------------------------------------------------------------------
_FALLBACK_RESPONSES: dict[str, str] = {
    # English
    "hi":           "Hello! How can I assist you today? 😊",
    "hello":        "Hello! How can I assist you today? 😊",
    "hey":          "Hey there! What can I do for you?",
    "good morning": "Good morning! ☀️ Hope you have a productive day. How can I help?",
    "good evening": "Good evening! 🌙 How can I assist you tonight?",
    "good afternoon": "Good afternoon! How can I help you today?",
    "good night":   "Good night! 😴 Have a great rest. See you tomorrow!",
    "thanks":       "You're welcome! Is there anything else I can help you with?",
    "thank you":    "You're welcome! Let me know if you need anything else. 😊",
    "thx":          "You're welcome! 😊",
    "bye":          "Goodbye! Have a great day! 👋",
    "goodbye":      "Goodbye! Talk to you soon. 👋",
    "how are you":  "I'm doing great, thank you for asking! 😊 How can I assist you today?",
    "what's up":    "All good! 😄 How can I help you today?",
    "whats up":     "All good! 😄 How can I help you today?",
    "sup":          "Hey! 😊 What can I do for you?",
    # Arabic
    "مرحبا":        "أهلاً وسهلاً! كيف يمكنني مساعدتك اليوم؟ 😊",
    "أهلا":         "أهلاً! كيف يمكنني مساعدتك؟",
    "اهلا":         "أهلاً! كيف يمكنني مساعدتك؟",
    "السلام عليكم": "وعليكم السلام! أهلاً بك، كيف يمكنني خدمتك؟",
    "هاي":          "هاي! 😊 كيف يمكنني مساعدتك اليوم؟",
    "هلا":          "هلا فيك! كيف أقدر أساعدك؟",
    "صباح الخير":   "صباح النور! ☀️ كيف يمكنني مساعدتك؟",
    "مساء الخير":   "مساء النور! 🌙 كيف يمكنني مساعدتك؟",
    "شكرا":         "العفو! هل تحتاج إلى أي شيء آخر؟ 😊",
    "شكراً":        "العفو! هل تحتاج إلى أي شيء آخر؟ 😊",
    "مع السلامة":   "مع السلامة! أتمنى لك يوماً رائعاً. 👋",
    "باي":          "باي! 👋 أتمنى لك يوماً جميلاً.",
    "كيف حالك":     "بخير شكراً لك! 😊 كيف يمكنني مساعدتك؟",
    "كيف الحال":    "الحمد لله بخير! كيف يمكنني خدمتك؟",
    "إزيك":         "بخير شكراً! 😊 ايه اللي تحتاجه؟",
    "ازيك":         "بخير شكراً! 😊 ايه اللي تحتاجه؟",
    "كيفك":         "بخير شكراً لك! كيف يمكنني مساعدتك؟",
    "مين انت":       "أنا Aria، السكرتيرة الذكية بتاعتك. كيف يمكنني مساعدتك؟",
    "انت مين":       "أنا Aria، مساعدتك الذكية.",
    "اسمك ايه":      "اسمي Aria.",
    "انت اسمك ايه":   "اسمي Aria.",
    "ما هو اسمك":    "اسمي Aria.",
    "who are you":   "I'm Aria, your AI assistant.",
    "what is your name": "My name is Aria.",
    "whats your name": "I'm Aria.",
    "what's your name": "I'm Aria.",
    "who am i talking to": "You are talking to Aria, your AI assistant."
}

# ---------------------------------------------------------------------------
# System persona prompt (injected into every conversational call)
# ---------------------------------------------------------------------------


async def conversational_node(state: AgentState, deps: AgentDeps) -> AgentState:
    """
    LangGraph node: handles casual greetings / chit-chat.

    Route here when: ClassifierController returns request_type == "conversational".
    Bypasses ALL search tools (web, Gmail, KB) and replies directly via LLM.

    Args:
        state (AgentState): The current LangGraph state.
        deps: The injected services (generation_client, etc.).

    Returns:
        AgentState: Updated state with result, status, and metadata.
    """
    generation_client = deps.generation_client
    query = state["query"]

    answer = ""

    if generation_client:
        user_prompt = render("agent", "conversational_user", query=query)

        llm_history = [
            generation_client.construct_prompt(
                prompt=render("agent", "conversational_persona"),
                role=generation_client.enums.SYSTEM.value,
            )
        ]

        state_history = state.get("chat_history", [])
        if state_history:
            for turn in state_history[:-1]:  # exclude the last one which is the current query
                role = generation_client.enums.USER.value if turn["role"] == "user" else generation_client.enums.ASSISTANT.value
                llm_history.append(generation_client.construct_prompt(prompt=turn["content"], role=role))

        try:
            answer = await generation_client.agenerate_text(prompt=user_prompt,
                chat_history=llm_history,
                max_output_tokens=150,
                temperature=0.7,
            )
        except Exception as exc:
            logger.error(f"[conversational_node] LLM call failed: {exc}")

    # ── Fallback if LLM unavailable or returned empty ──────────────────────
    if not answer:
        key = query.strip().lower().rstrip("?!.،, ")
        answer = _FALLBACK_RESPONSES.get(key, "Hello! I am Aria, your AI assistant. How can I help?")

    state["result"] = answer
    state["status"] = "success"
    state["metadata"]["source"] = "conversational"

    logger.info("[conversational_node] replied to a greeting")
    return state
