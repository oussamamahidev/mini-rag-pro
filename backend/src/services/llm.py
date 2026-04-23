"""OpenAI chat-completion service for RAG answer generation."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from ..config import Settings
from ..logging_config import get_logger
from .vector_store import SearchResult

logger = get_logger(__name__)
llm_service: "LLMService | None" = None

MODEL_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "gpt-4": (0.03, 0.06),
    "gpt-4-turbo": (0.01, 0.03),
}


class LLMService:
    """Generate grounded RAG answers from retrieved context chunks."""

    RAG_SYSTEM_PROMPT = """You are a helpful assistant that answers questions based strictly on the provided context.

Rules:
1. Answer ONLY using information from the provided context.
2. If the answer is not in the context, say "I don't have enough information in the provided documents to answer this."
3. Be concise and direct. Do not pad your answer.
4. If you quote from the context, indicate which document it's from.
5. Never make up information not present in the context."""

    def __init__(self, settings: Settings) -> None:
        """Create an OpenAI chat-completion client."""
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.default_model = settings.openai_model

    async def generate_rag_answer(
        self,
        query: str,
        chunks: list[SearchResult],
        conversation_history: list[dict[str, str]] | None = None,
        model: str | None = None,
    ) -> tuple[str, int, int]:
        """Generate a grounded answer and return answer text plus token usage."""
        resolved_model = model or self.default_model
        messages = self._build_rag_messages(query, chunks, conversation_history or [])
        response = await self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            temperature=0.1,
            stream=False,
        )

        answer = response.choices[0].message.content or ""
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return answer.strip(), prompt_tokens, completion_tokens

    async def generate_answer_stream(
        self,
        query: str,
        chunks: list[SearchResult],
        conversation_history: list[dict[str, str]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield a grounded answer token stream suitable for SSE."""
        messages = self._build_rag_messages(query, chunks, conversation_history or [])
        stream = await self.client.chat.completions.create(
            model=self.default_model,
            messages=messages,
            temperature=0.1,
            stream=True,
        )
        async for chunk in stream:
            token = chunk.choices[0].delta.content
            if token:
                yield token

    async def generate_hypothesis(self, query: str) -> str:
        """Generate a hypothetical answer for HyDE retrieval."""
        response = await self.client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "user",
                    "content": f"Write a 3-sentence paragraph that would answer this question: {query}",
                }
            ],
            max_tokens=200,
            temperature=0.7,
            stream=False,
        )
        return (response.choices[0].message.content or "").strip()

    async def generate_direct_answer(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        model: str | None = None,
    ) -> tuple[str, int, int]:
        """Generate a short direct answer for trivial non-document questions."""
        resolved_model = model or self.default_model
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Answer the user's trivial question directly and concisely. "
                    "Do not claim to have inspected uploaded documents."
                ),
            }
        ]
        messages.extend(self._sanitize_history(conversation_history or [])[-4:])
        messages.append({"role": "user", "content": query})
        response = await self.client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            max_tokens=200,
            temperature=0.1,
            stream=False,
        )
        answer = response.choices[0].message.content or ""
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return answer.strip(), prompt_tokens, completion_tokens

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: str) -> float:
        """Estimate OpenAI chat-completion cost in USD."""
        input_rate, output_rate = MODEL_PRICING_PER_1K.get(model, MODEL_PRICING_PER_1K["gpt-3.5-turbo"])
        return (prompt_tokens / 1000 * input_rate) + (completion_tokens / 1000 * output_rate)

    async def close(self) -> None:
        """Close the OpenAI client."""
        close_result = self.client.close()
        if hasattr(close_result, "__await__"):
            await close_result

    def _build_rag_messages(
        self,
        query: str,
        chunks: list[SearchResult],
        conversation_history: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Build system, history, and context-separated user messages."""
        context = self._build_context(chunks)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"{self.RAG_SYSTEM_PROMPT}\n\n"
                    "Conversation history is provided only for continuity. "
                    "If conversation history conflicts with retrieved context, ignore the history. "
                    "When feasible, cite supporting context using the source labels."
                ),
            }
        ]
        messages.extend(self._sanitize_history(conversation_history)[-6:])
        messages.append(
            {
                "role": "user",
                "content": (
                    "Retrieved context:\n"
                    f"{context}\n\n"
                    "Use only the retrieved context above. Include source markers when feasible.\n\n"
                    f"Question: {query}"
                ),
            }
        )
        return messages

    def _build_context(self, chunks: list[SearchResult]) -> str:
        """Format retrieved chunks for the RAG prompt."""
        return "\n---\n".join(
            f"[Source: {chunk.document_name}, chunk {chunk.chunk_index}]\n{chunk.text}\n"
            for chunk in chunks
        )

    def _sanitize_history(self, conversation_history: list[dict[str, str]]) -> list[dict[str, str]]:
        """Keep only supported role/content pairs from chat history."""
        sanitized: list[dict[str, str]] = []
        for message in conversation_history:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not content:
                continue
            sanitized.append({"role": role, "content": str(content)[:4000]})
        return sanitized


def initialize_llm_service(settings: Settings) -> LLMService:
    """Create and store the module-level LLM service singleton."""
    global llm_service
    llm_service = LLMService(settings)
    return llm_service


def get_llm_service() -> LLMService:
    """Return the initialized LLM service singleton."""
    if llm_service is None:
        raise RuntimeError("LLMService has not been initialized")
    return llm_service


def get_openai_client() -> AsyncOpenAI:
    """Return the shared AsyncOpenAI client from the LLM service."""
    return get_llm_service().client
