import json
from app.llm.base import LLMProvider
from app.pipeline.result_sampling import sample_for_llm


SYSTEM_PROMPT = """You are a data analyst summarizing Splunk query results. Given the user's original question, the SPL query that was run, and the results, provide a concise, insight-focused summary.

Rules:
- Be concise — 2-4 sentences
- Highlight the most important findings
- Mention specific numbers and values
- If results are empty, say so clearly
- Do not explain the SPL query itself"""


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
            system_prompt=SYSTEM_PROMPT,
            history=history or [],
            user_prompt=user_prompt,
        )
        return response.content

from app.pipeline.prompt_registry import register as _reg_prompt
_reg_prompt("summarizer", SYSTEM_PROMPT)
