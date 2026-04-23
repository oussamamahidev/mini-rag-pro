"""External tools used by the agentic query layer."""

from __future__ import annotations

import asyncio
from typing import Any

from duckduckgo_search import DDGS

from ..logging_config import get_logger

logger = get_logger(__name__)


class WebSearchTool:
    """Free DuckDuckGo web search plus OpenAI summarization."""

    def __init__(self, openai_client: Any, model: str = "gpt-3.5-turbo") -> None:
        """Create the web search tool."""
        self.client = openai_client
        self.model = model

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search DuckDuckGo and return title/url/snippet dictionaries."""
        max_results = max(1, min(max_results, 10))

        def _sync_search() -> list[dict[str, Any]]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        try:
            loop = asyncio.get_running_loop()
            results = await asyncio.wait_for(
                loop.run_in_executor(None, _sync_search),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("web search timed out")
            return []
        except Exception as exc:
            logger.error("web search failed: %s", exc)
            return []

        normalized_results: list[dict[str, str]] = []
        for result in results:
            title = str(result.get("title") or "").strip()
            url = str(result.get("href") or result.get("url") or "").strip()
            snippet = str(result.get("body") or result.get("snippet") or "").strip()
            if not title or not url:
                continue
            normalized_results.append({"title": title, "url": url, "snippet": snippet})
        return normalized_results

    async def search_and_answer(self, query: str) -> tuple[str, list[dict[str, str]]]:
        """Search the web and generate a concise sourced answer."""
        results = await self.search(query)
        if not results:
            return "I couldn't find current information on this topic.", []

        context = "\n\n".join(
            f"[{result['title']}] ({result['url']})\n{result['snippet']}"
            for result in results
        )
        prompt = f"""Based on these web search results, answer the question concisely.
Cite sources by mentioning the result title. If results are insufficient, say so.

Question: {query}

Search Results:
{context}

Answer:"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.3,
            stream=False,
        )
        answer = response.choices[0].message.content or ""
        return answer.strip(), results
