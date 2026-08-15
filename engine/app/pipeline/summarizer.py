from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.result_sampling import format_results_for_llm


class Summarizer:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def summarize(self, query: str, spl: str, results: list[dict], history: list[dict] | None = None) -> str:
        """Summarize query results in natural language."""
        results_str = format_results_for_llm(results, k=60, empty_text="No results returned.")

        user_prompt = f"""Original question: {query}

SPL query executed: {spl}

Results:
{results_str}"""

        response = await self._llm.generate(
            system_prompt=prompt_registry.get("summarizer"),
            history=history or [],
            user_prompt=user_prompt,
        )
        return response.content
