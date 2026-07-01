import json
from app.llm.base import LLMProvider
from app.pipeline import prompt_registry
from app.pipeline.result_sampling import sample_for_llm


class Summarizer:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def summarize(self, query: str, spl: str, results: list[dict], history: list[dict] | None = None) -> str:
        """Summarize query results in natural language."""
        sample, sketch = sample_for_llm(results, k=60) if results else ([], {})
        results_str = json.dumps(sample, indent=2) if sample else "No results returned."
        if sketch.get("total_rows", 0) > len(sample):
            results_str += f"\n\n(Showing {sketch['sampled']} of {sketch['total_rows']} total rows. Fields seen: {', '.join(sketch['fields'])})"

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
