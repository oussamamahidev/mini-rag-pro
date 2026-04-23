"""Query routing agent for selecting RAG, web, direct, or clarification flows."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..logging_config import get_logger

logger = get_logger(__name__)

VALID_DECISIONS = {"rag", "web_search", "direct", "clarify"}
VALID_RETRIEVAL_STRATEGIES = {"vanilla", "hybrid", "rerank", "hyde"}

ROUTER_SYSTEM_PROMPT = """You are a query classifier for a document-based RAG system.
Classify the user's question to determine the best strategy.

STRATEGIES:
- "rag": Question about content likely in uploaded documents. DEFAULT choice.
- "web_search": Requires real-time information: today's news, current prices, live sports scores,
  current weather, recent events after 2024. Only use when time-sensitive.
- "direct": Trivial factual knowledge: basic math (2+2), dictionary definitions,
  well-known historical dates, programming syntax. No documents needed.
- "clarify": Question is genuinely unanswerable without more context.
  Only use when the question is completely ambiguous (not just broad).

RAG RETRIEVAL STRATEGY:
- "hybrid": exact term, title, code, identifier, acronym, or quoted-string query.
- "hyde": semantic explanatory query asking why/how/what it means.
- "rerank": high-precision query where exact top evidence matters.
- "vanilla": general semantic query.

RULES:
- Default to "rag" when uncertain
- "web_search" only for explicitly time-sensitive queries
- "web_search" is forbidden when the user asks about uploaded documents/files only
- "direct" only for genuinely trivial questions
- "clarify" is a last resort

Return ONLY a JSON object, no other text:
{"decision": "<strategy>", "reason": "<15 words max>", "confidence": <0.0-1.0>, "reason_code": "<snake_case>", "retrieval_strategy": "<strategy-or-null>"}"""


@dataclass(slots=True)
class RoutingDecision:
    """Strict structured route selected for a query."""

    decision: str
    reason: str
    confidence: float
    reason_code: str = "classifier"
    retrieval_strategy: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalize route fields into the supported structured value set."""
        self.decision = self.decision if self.decision in VALID_DECISIONS else "rag"
        self.reason = self.reason.strip()[:120] or "router decision"
        self.confidence = clamp_float(self.confidence, default=0.5)
        self.reason_code = sanitize_reason_code(self.reason_code)
        if self.decision != "rag":
            self.retrieval_strategy = None
        elif self.retrieval_strategy is not None:
            self.retrieval_strategy = normalize_retrieval_strategy(self.retrieval_strategy)


class QueryRouter:
    """Classify incoming user queries before retrieval or answer generation."""

    def __init__(self, openai_client: Any, model: str = "gpt-3.5-turbo") -> None:
        """Create a router backed by deterministic rules and a fast LLM classifier."""
        self.client = openai_client
        self.model = model
        self.timeout_seconds = 5.0

    async def route(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        project_default_strategy: str = "hybrid",
    ) -> RoutingDecision:
        """Classify the query, falling back to RAG on timeout or classifier failure."""
        history = conversation_history or []
        default_strategy = normalize_retrieval_strategy(project_default_strategy)
        deterministic = self._deterministic_route(query, history, default_strategy)
        if deterministic is not None:
            logger.info(
                "query routed by rule decision=%s reason_code=%s confidence=%.2f strategy=%s",
                deterministic.decision,
                deterministic.reason_code,
                deterministic.confidence,
                deterministic.retrieval_strategy,
            )
            return deterministic

        messages = [{"role": "system", "content": ROUTER_SYSTEM_PROMPT}]
        messages.extend(sanitize_history(history)[-4:])
        messages.append({"role": "user", "content": query})

        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=120,
                ),
                timeout=self.timeout_seconds,
            )
            raw_content = response.choices[0].message.content or "{}"
            parsed = json.loads(raw_content)
            decision = self._decision_from_classifier(parsed, query, default_strategy)
            logger.info(
                "query routed by classifier decision=%s reason_code=%s confidence=%.2f strategy=%s",
                decision.decision,
                decision.reason_code,
                decision.confidence,
                decision.retrieval_strategy,
            )
            return decision
        except asyncio.TimeoutError:
            logger.warning("router timed out; defaulting to rag")
            return self._fallback_decision(query, default_strategy, "timeout_fallback", "timeout fallback")
        except Exception as exc:
            logger.warning("router error=%s; defaulting to rag", exc)
            return self._fallback_decision(query, default_strategy, "classifier_error", "fallback due to error")

    def _deterministic_route(
        self,
        query: str,
        conversation_history: list[dict[str, str]],
        default_strategy: str,
    ) -> RoutingDecision | None:
        """Return a rule-based route for obvious cases."""
        normalized = normalize_query(query)
        if not normalized:
            return RoutingDecision("clarify", "empty query", 0.95, "empty_query", None)

        analysis = analyze_query_signals(normalized)
        base_metadata = {
            "router_source": "rules",
            "signals": analysis,
            "history_message_count": len(conversation_history),
            "false_positive_risk": classify_false_positive_risk(analysis),
            "false_negative_risk": classify_false_negative_risk(analysis),
        }

        if analysis["uploaded_docs_only"]:
            return RoutingDecision(
                decision="rag",
                reason="User asked about uploaded documents.",
                confidence=0.98,
                reason_code="uploaded_docs_requested",
                retrieval_strategy=predict_retrieval_strategy(query, default_strategy),
                metadata=base_metadata,
            )

        if analysis["simple_math"]:
            return RoutingDecision(
                decision="direct",
                reason="Simple calculation needs no documents.",
                confidence=0.98,
                reason_code="simple_math",
                retrieval_strategy=None,
                metadata=base_metadata,
            )

        if analysis["trivial_direct"]:
            return RoutingDecision(
                decision="direct",
                reason="Trivial general knowledge question.",
                confidence=0.88,
                reason_code="trivial_knowledge",
                retrieval_strategy=None,
                metadata=base_metadata,
            )

        if analysis["time_sensitive"]:
            return RoutingDecision(
                decision="web_search",
                reason="Question asks for current information.",
                confidence=0.92,
                reason_code="current_information",
                retrieval_strategy=None,
                metadata=base_metadata,
            )

        if analysis["ambiguous"]:
            return RoutingDecision(
                decision="clarify",
                reason="Question is too ambiguous.",
                confidence=0.82,
                reason_code="ambiguous_reference",
                retrieval_strategy=None,
                metadata=base_metadata,
            )

        return None

    def _decision_from_classifier(
        self,
        parsed: dict[str, Any],
        query: str,
        default_strategy: str,
    ) -> RoutingDecision:
        """Validate and normalize a classifier JSON response."""
        signals = analyze_query_signals(normalize_query(query))
        decision = str(parsed.get("decision", "rag")).strip().lower()
        if decision not in VALID_DECISIONS:
            decision = "rag"

        if decision == "web_search" and signals["uploaded_docs_only"]:
            decision = "rag"

        reason = str(parsed.get("reason") or "classifier decision").strip()[:120]
        confidence = clamp_float(parsed.get("confidence", 0.8), default=0.8)
        reason_code = sanitize_reason_code(str(parsed.get("reason_code") or "llm_classifier"))

        retrieval_strategy = None
        if decision == "rag":
            retrieval_strategy = normalize_retrieval_strategy(
                str(parsed.get("retrieval_strategy") or "")
            )
            if parsed.get("retrieval_strategy") is None or retrieval_strategy == "hybrid":
                retrieval_strategy = predict_retrieval_strategy(query, default_strategy)

        return RoutingDecision(
            decision=decision,
            reason=reason,
            confidence=confidence,
            reason_code=reason_code,
            retrieval_strategy=retrieval_strategy,
            metadata={
                "router_source": "llm_classifier",
                "signals": signals,
                "raw_decision": parsed,
                "false_positive_risk": classify_false_positive_risk(signals),
                "false_negative_risk": classify_false_negative_risk(signals),
            },
        )

    def _fallback_decision(
        self,
        query: str,
        default_strategy: str,
        reason_code: str,
        reason: str,
    ) -> RoutingDecision:
        """Build a safe RAG fallback decision."""
        signals = analyze_query_signals(normalize_query(query))
        return RoutingDecision(
            decision="rag",
            reason=reason,
            confidence=0.5,
            reason_code=reason_code,
            retrieval_strategy=predict_retrieval_strategy(query, default_strategy),
            metadata={
                "router_source": "fallback",
                "signals": signals,
                "false_positive_risk": classify_false_positive_risk(signals),
                "false_negative_risk": classify_false_negative_risk(signals),
            },
        )


def predict_retrieval_strategy(query: str, project_default_strategy: str = "hybrid") -> str:
    """Predict the best retrieval strategy for a RAG query."""
    normalized = normalize_query(query)
    if re.search(r'"[^"]+"|\'[^\']+\'|`[^`]+`', query):
        return "hybrid"
    if re.search(r"\b(id|identifier|sku|invoice|section|clause|policy|code|error|exact|verbatim)\b", normalized):
        return "hybrid"
    if re.search(r"\b(cite|quote|prove|evidence|source|which document|where exactly|highest confidence)\b", normalized):
        return "rerank"
    if re.search(r"\b(why|how|explain|summarize|compare|meaning|interpret|relationship|implications?)\b", normalized):
        return "hyde"
    return normalize_retrieval_strategy(project_default_strategy)


def analyze_query_signals(normalized_query: str) -> dict[str, Any]:
    """Extract router signals used for rules, safeguards, and diagnostics."""
    uploaded_docs_only = bool(
        re.search(
            r"\b(my|uploaded|provided|these|the)\s+(docs?|documents?|files?|uploads?)\b|"
            r"\b(in|from|according to|based on)\s+(the\s+)?(docs?|documents?|files?|uploads?|context)\b",
            normalized_query,
        )
    )
    time_sensitive = bool(
        re.search(
            r"\b(today|tonight|now|current|currently|latest|recent|breaking|news|weather|forecast|"
            r"stock price|market price|crypto|score|sports|live|this week|this month|this year|"
            r"after 2024|2025|2026)\b",
            normalized_query,
        )
    )
    simple_math = bool(
        re.fullmatch(r"(what is|calculate|compute|solve)?\s*[-+*/().\d\s]+(\?)?", normalized_query)
        and re.search(r"\d", normalized_query)
        and re.search(r"[-+*/]", normalized_query)
    )
    trivial_direct = bool(
        re.search(
            r"^(define|what does .+ mean|what is the capital of|when was .+ born|"
            r"what is \d+\s*[-+*/]\s*\d+|how do i (write|use) .+ syntax)",
            normalized_query,
        )
    )
    ambiguous = normalized_query in {"it", "that", "this", "what about it", "what about that", "explain that"}
    return {
        "uploaded_docs_only": uploaded_docs_only,
        "time_sensitive": time_sensitive,
        "simple_math": simple_math,
        "trivial_direct": trivial_direct,
        "ambiguous": ambiguous,
    }


def classify_false_positive_risk(signals: dict[str, Any]) -> str:
    """Return a compact diagnostic for likely over-routing."""
    if signals["uploaded_docs_only"] and signals["time_sensitive"]:
        return "web_search_blocked_by_document_scope"
    if signals["time_sensitive"]:
        return "web_search_may_answer_external_when_docs_needed"
    if signals["trivial_direct"]:
        return "direct_may_skip_relevant_docs"
    return "low"


def classify_false_negative_risk(signals: dict[str, Any]) -> str:
    """Return a compact diagnostic for likely under-routing."""
    if signals["time_sensitive"]:
        return "missing_current_info_if_rag"
    if signals["uploaded_docs_only"]:
        return "missing_docs_if_not_rag"
    if signals["ambiguous"]:
        return "may_need_clarification"
    return "low"


def normalize_query(query: str) -> str:
    """Lowercase and collapse query whitespace."""
    return re.sub(r"\s+", " ", query.strip().lower())


def normalize_retrieval_strategy(value: str | None) -> str:
    """Return a supported retrieval strategy with hybrid as the safe default."""
    normalized = (value or "").strip().lower()
    return normalized if normalized in VALID_RETRIEVAL_STRATEGIES else "hybrid"


def sanitize_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep only role/content chat history messages supported by OpenAI chat."""
    sanitized: list[dict[str, str]] = []
    for message in history:
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not content:
            continue
        sanitized.append({"role": role, "content": str(content)[:2000]})
    return sanitized


def sanitize_reason_code(value: str) -> str:
    """Normalize classifier reason codes to short snake_case strings."""
    reason_code = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return (reason_code or "llm_classifier")[:60]


def clamp_float(value: Any, default: float) -> float:
    """Convert and clamp a confidence value into [0, 1]."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(1.0, number)), 3)
