"""RAGAS and heuristic evaluation for completed RAG queries."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

EVALUATION_VERSION = "ragas-v1"
RAGAS_TIMEOUT_SECONDS = 120.0
APPROXIMATE_RAGAS_COST_USD = 0.005
DEFAULT_EVALUATION_MODEL = "ragas-default-openai"

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
    "your",
}


@dataclass(slots=True)
class EvaluationResult:
    """Scores and metadata produced by a RAG response evaluation."""

    faithfulness: float | None
    answer_relevancy: float | None
    context_precision: float | None
    context_recall: float | None
    evaluation_cost_usd: float
    error: str | None = None
    evaluation_runtime_ms: float = 0.0
    evaluation_version: str = EVALUATION_VERSION
    evaluation_backend: str = "ragas"
    evaluation_provider: str = "ragas"
    evaluation_model: str = DEFAULT_EVALUATION_MODEL
    evaluation_model_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_scores(self) -> bool:
        """Return whether at least one metric score is available."""
        return any(
            score is not None
            for score in (
                self.faithfulness,
                self.answer_relevancy,
                self.context_precision,
                self.context_recall,
            )
        )


async def run_ragas_evaluation(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None = None,
) -> EvaluationResult:
    """
    Run RAGAS evaluation on a single RAG response.

    RAGAS evaluate() is synchronous, so the call runs in a thread pool. If RAGAS
    is unavailable or fails, a deterministic lexical heuristic is used as a
    lightweight fallback. This function never raises.
    """
    started_at = time.perf_counter()
    clean_contexts = [context.strip() for context in contexts if context and context.strip()]
    clean_answer = answer.strip()

    if not clean_answer:
        return _empty_result(started_at, "empty answer")
    if not clean_contexts:
        return _empty_result(started_at, "no contexts")

    try:
        loop = asyncio.get_running_loop()
        result, ragas_version = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _run_ragas_sync,
                question,
                clean_answer,
                clean_contexts,
                ground_truth,
            ),
            timeout=RAGAS_TIMEOUT_SECONDS,
        )
        return EvaluationResult(
            faithfulness=_extract_metric(result, "faithfulness"),
            answer_relevancy=_extract_metric(result, "answer_relevancy"),
            context_precision=_extract_metric(result, "context_precision"),
            context_recall=_extract_metric(result, "context_recall"),
            evaluation_cost_usd=APPROXIMATE_RAGAS_COST_USD,
            evaluation_runtime_ms=_elapsed_ms(started_at),
            evaluation_backend="ragas",
            evaluation_provider="ragas",
            evaluation_model=DEFAULT_EVALUATION_MODEL,
            evaluation_model_version=ragas_version,
            metadata={
                "ragas_version": ragas_version,
                "metrics": _requested_metrics(ground_truth),
                "context_count": len(clean_contexts),
                "fallback_used": False,
            },
        )
    except asyncio.TimeoutError:
        return _heuristic_result(
            question,
            clean_answer,
            clean_contexts,
            ground_truth,
            started_at,
            "evaluation timeout; heuristic fallback used",
            attempted_ragas=True,
        )
    except Exception as exc:
        return _heuristic_result(
            question,
            clean_answer,
            clean_contexts,
            ground_truth,
            started_at,
            f"evaluation error: {str(exc)[:200]}; heuristic fallback used",
            attempted_ragas=True,
        )


def _run_ragas_sync(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None,
) -> tuple[Any, str | None]:
    """Import and run RAGAS in a synchronous worker thread."""
    from datasets import Dataset
    from ragas import evaluate
    import ragas
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    data: dict[str, list[Any]] = {
        "question": [question],
        "answer": [answer],
        "contexts": [contexts],
    }
    if ground_truth:
        data["ground_truth"] = [ground_truth]

    metrics = [faithfulness, answer_relevancy, context_precision]
    if ground_truth:
        metrics.append(context_recall)

    dataset = Dataset.from_dict(data)
    result = evaluate(dataset, metrics=metrics, raise_exceptions=False)
    return result, getattr(ragas, "__version__", None)


def _empty_result(started_at: float, error: str) -> EvaluationResult:
    """Return a failed evaluation result for invalid input."""
    return EvaluationResult(
        faithfulness=None,
        answer_relevancy=None,
        context_precision=None,
        context_recall=None,
        evaluation_cost_usd=0.0,
        error=error,
        evaluation_runtime_ms=_elapsed_ms(started_at),
        evaluation_backend="none",
        evaluation_provider="none",
        evaluation_model="none",
        metadata={"fallback_used": False},
    )


def _heuristic_result(
    question: str,
    answer: str,
    contexts: list[str],
    ground_truth: str | None,
    started_at: float,
    error: str,
    *,
    attempted_ragas: bool,
) -> EvaluationResult:
    """Produce inexpensive lexical scores when RAGAS cannot return scores."""
    return EvaluationResult(
        faithfulness=_score_faithfulness(answer, contexts),
        answer_relevancy=_score_answer_relevancy(question, answer),
        context_precision=_score_context_precision(question, answer, contexts),
        context_recall=_score_context_recall(ground_truth, contexts),
        evaluation_cost_usd=APPROXIMATE_RAGAS_COST_USD if attempted_ragas else 0.0,
        error=error,
        evaluation_runtime_ms=_elapsed_ms(started_at),
        evaluation_backend="heuristic",
        evaluation_provider="lexical-heuristic",
        evaluation_model="token-overlap-v1",
        evaluation_model_version=EVALUATION_VERSION,
        metadata={
            "metrics": _requested_metrics(ground_truth),
            "context_count": len(contexts),
            "fallback_used": True,
            "ragas_attempted": attempted_ragas,
        },
    )


def _score_faithfulness(answer: str, contexts: list[str]) -> float | None:
    """Estimate how much of the answer is lexically supported by context."""
    context_tokens = _tokenize(" ".join(contexts))
    if not context_tokens:
        return None

    sentences = [sentence for sentence in re.split(r"[.!?\n]+", answer) if sentence.strip()]
    if not sentences:
        return None

    sentence_scores: list[float] = []
    for sentence in sentences:
        sentence_tokens = _tokenize(sentence)
        if not sentence_tokens:
            continue
        overlap = len(sentence_tokens & context_tokens) / len(sentence_tokens)
        sentence_scores.append(min(1.0, overlap / 0.65))

    if not sentence_scores:
        return None
    return _round_score(sum(sentence_scores) / len(sentence_scores))


def _score_answer_relevancy(question: str, answer: str) -> float | None:
    """Estimate whether the answer addresses the question."""
    question_tokens = _tokenize(question)
    answer_tokens = _tokenize(answer)
    if not question_tokens or not answer_tokens:
        return None

    precision = len(question_tokens & answer_tokens) / len(answer_tokens)
    recall = len(question_tokens & answer_tokens) / len(question_tokens)
    if precision + recall == 0:
        return 0.0
    return _round_score((2 * precision * recall) / (precision + recall))


def _score_context_precision(question: str, answer: str, contexts: list[str]) -> float | None:
    """Estimate the share of retrieved chunks that appear useful."""
    focus_tokens = _tokenize(f"{question} {answer}")
    if not contexts or not focus_tokens:
        return None

    chunk_scores: list[float] = []
    for context in contexts:
        context_tokens = _tokenize(context)
        if not context_tokens:
            chunk_scores.append(0.0)
            continue
        overlap = len(context_tokens & focus_tokens) / min(len(context_tokens), len(focus_tokens))
        chunk_scores.append(min(1.0, overlap / 0.35))

    return _round_score(sum(chunk_scores) / len(chunk_scores))


def _score_context_recall(ground_truth: str | None, contexts: list[str]) -> float | None:
    """Estimate context recall when a ground truth answer is available."""
    if not ground_truth:
        return None
    ground_truth_tokens = _tokenize(ground_truth)
    context_tokens = _tokenize(" ".join(contexts))
    if not ground_truth_tokens or not context_tokens:
        return None
    return _round_score(len(ground_truth_tokens & context_tokens) / len(ground_truth_tokens))


def _tokenize(text: str) -> set[str]:
    """Tokenize text into normalized content words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if len(token) > 2 and token not in STOPWORDS}


def _requested_metrics(ground_truth: str | None) -> list[str]:
    """Return metric names requested for the evaluation."""
    metrics = ["faithfulness", "answer_relevancy", "context_precision"]
    if ground_truth:
        metrics.append("context_recall")
    return metrics


def _extract_metric(result: Any, metric_name: str) -> float | None:
    """Extract one metric from the possible RAGAS result shapes."""
    value = None
    if isinstance(result, Mapping):
        value = result.get(metric_name)

    if value is None:
        getter = getattr(result, "get", None)
        if callable(getter):
            try:
                value = getter(metric_name)
            except Exception:
                value = None

    if value is None:
        try:
            value = result[metric_name]
        except Exception:
            value = None

    if value is None:
        scores = getattr(result, "scores", None)
        if isinstance(scores, list) and scores and isinstance(scores[0], Mapping):
            value = scores[0].get(metric_name)

    if value is None:
        to_pandas = getattr(result, "to_pandas", None)
        if callable(to_pandas):
            try:
                dataframe = to_pandas()
                if metric_name in dataframe.columns and len(dataframe.index) > 0:
                    value = dataframe.iloc[0][metric_name]
            except Exception:
                value = None

    return _safe_float(value)


def _safe_float(value: Any) -> float | None:
    """Convert RAGAS output to a bounded float. Returns None on failure."""
    try:
        if value is None:
            return None
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            if not value:
                return None
            value = value[0]
        score = float(value)
        if math.isnan(score):
            return None
        return _round_score(score)
    except (TypeError, ValueError):
        return None


def _round_score(value: float) -> float:
    """Clamp and round a score to the 0.0-1.0 range."""
    return round(max(0.0, min(1.0, value)), 4)


def _elapsed_ms(started_at: float) -> float:
    """Return elapsed milliseconds since a perf_counter timestamp."""
    return round((time.perf_counter() - started_at) * 1000, 2)
