"""Base interfaces for retrieval strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class RetrievedChunk:
    """A document chunk retrieved for a query."""

    chunk_id: str
    document_id: str
    document_name: str
    text: str
    score: float
    page_number: int | None
    chunk_index: int
    strategy_used: str


class BaseRetriever(ABC):
    """Abstract base class for all retrieval strategies."""

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        project_id: str,
        tenant_id: str,
        top_k: int = 5,
    ) -> tuple[list[RetrievedChunk], float]:
        """
        Retrieve relevant chunks for a query.

        Returns the retrieved chunks and retrieval latency in milliseconds.
        """

    @abstractmethod
    def get_strategy_name(self) -> str:
        """Return the strategy identifier string."""

