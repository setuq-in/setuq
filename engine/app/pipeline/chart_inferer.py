import re
from app.api.schemas import ChartSpec

ROW_CAP = 1000


def _is_numeric(value: object) -> bool:
    if value is None:
        return False
    try:
        float(str(value))
        return True
    except (ValueError, TypeError):
        return False


def _extract_by_fields(spl: str) -> set[str]:
    """Extract field names listed after a 'by' clause in stats/timechart/chart commands.

    Handles patterns like:
      | stats count by src_ip dest_port
      | timechart count by status
      | chart avg(cpu) by host
    Returns a set of field names that are grouping dimensions and must be
    treated as categorical regardless of their value type.
    """
    # Match 'by' followed by one or more identifiers (no pipe or keyword after)
    pattern = re.compile(
        r"\bby\s+((?:[A-Za-z_][A-Za-z0-9_.]*\s*)+)",
        re.IGNORECASE,
    )
    # SPL keywords that can legally appear inside a `by` clause (e.g. `by host AS h`).
    # Drop them so they aren't treated as field names.
    spl_keywords = {"as", "where", "by", "eval"}
    fields: set[str] = set()
    for match in pattern.finditer(spl):
        for field in match.group(1).split():
            field = field.strip()
            if field and field.lower() not in spl_keywords:
                fields.add(field)
    return fields


def _classify_columns(rows: list[dict], by_fields: set[str] | None = None) -> tuple[list[str], list[str], bool]:
    """Return (numeric_cols, categorical_cols, has_time) from the first row.

    Columns named in *by_fields* are always categorical (they are SPL grouping
    dimensions) even when their sample values happen to be numeric strings
    (e.g. port numbers, HTTP status codes).
    """
    if not rows:
        return [], [], False
    by_fields = by_fields or set()
    first = rows[0]
    keys = list(first.keys())
    has_time = "_time" in keys
    numeric_cols: list[str] = []
    categorical_cols: list[str] = []
    for key in keys:
        if key == "_time":
            continue
        if key in by_fields:
            categorical_cols.append(key)
            continue
        sample_values = [r.get(key) for r in rows[:10] if r.get(key) is not None]
        if sample_values and all(_is_numeric(v) for v in sample_values):
            numeric_cols.append(key)
        else:
            categorical_cols.append(key)
    return numeric_cols, categorical_cols, has_time


def _looks_like_freeform_events(rows: list[dict]) -> bool:
    """Detect raw event listings (have _raw or _bkt; no aggregations)."""
    if not rows:
        return False
    first_keys = set(rows[0].keys())
    return "_raw" in first_keys or "_bkt" in first_keys


def _looks_like_threshold_metric(spl: str) -> bool:
    """Detect SPL patterns that suggest a gauge fit (avg/max/min over a metric)."""
    pattern = re.compile(r"\bstats\s+(?:avg|max|min)\b", re.IGNORECASE)
    return bool(pattern.search(spl))


def infer_heuristic(spl: str, rows: list[dict]) -> ChartSpec | None:
    """Pure heuristic chart inference. Returns None when not chartable."""
    if not rows:
        return None
    if _looks_like_freeform_events(rows):
        return None

    by_fields = _extract_by_fields(spl)
    numeric_cols, categorical_cols, has_time = _classify_columns(rows, by_fields)
    n_rows = len(rows)

    # Single-row cases (always allowed regardless of row cap)
    if n_rows == 1 and len(numeric_cols) == 1 and not categorical_cols and not has_time:
        if _looks_like_threshold_metric(spl):
            return ChartSpec(
                chart_type="gauge",
                x_field=None,
                y_fields=numeric_cols,
                series_field=None,
                title=f"{numeric_cols[0]}",
                confidence=0.8,
            )
        return ChartSpec(
            chart_type="single_value",
            x_field=None,
            y_fields=numeric_cols,
            series_field=None,
            title=f"{numeric_cols[0]}",
            confidence=1.0,
        )

    # Apply row cap for non-single-value charts
    if n_rows > ROW_CAP:
        return None

    # Time-series cases
    if has_time and numeric_cols and not categorical_cols:
        return ChartSpec(
            chart_type="line",
            x_field="_time",
            y_fields=numeric_cols,
            series_field=None,
            title=f"{', '.join(numeric_cols)} over time",
            confidence=1.0,
        )
    if has_time and len(categorical_cols) == 1 and len(numeric_cols) == 1:
        return ChartSpec(
            chart_type="stacked_bar",
            x_field="_time",
            y_fields=numeric_cols,
            series_field=categorical_cols[0],
            title=f"{numeric_cols[0]} over time by {categorical_cols[0]}",
            confidence=0.8,
        )

    # Two categorical + one numeric → heatmap
    if not has_time and len(categorical_cols) == 2 and len(numeric_cols) == 1:
        return ChartSpec(
            chart_type="heatmap",
            x_field=categorical_cols[0],
            y_fields=numeric_cols,
            series_field=categorical_cols[1],
            title=f"{numeric_cols[0]} by {categorical_cols[0]} × {categorical_cols[1]}",
            confidence=0.9,
        )

    # One categorical + one numeric → pie (≤8) or bar
    if not has_time and len(categorical_cols) == 1 and len(numeric_cols) == 1:
        chart_type = "pie" if n_rows <= 8 else "bar"
        return ChartSpec(
            chart_type=chart_type,
            x_field=categorical_cols[0],
            y_fields=numeric_cols,
            series_field=None,
            title=f"{numeric_cols[0]} by {categorical_cols[0]}",
            confidence=0.9,
        )

    # Two numerics, no time, no cat → scatter
    if not has_time and not categorical_cols and len(numeric_cols) == 2:
        return ChartSpec(
            chart_type="scatter",
            x_field=numeric_cols[0],
            y_fields=[numeric_cols[1]],
            series_field=None,
            title=f"{numeric_cols[1]} vs {numeric_cols[0]}",
            confidence=0.8,
        )

    # Three numerics, no time, no cat → bubble
    if not has_time and not categorical_cols and len(numeric_cols) == 3:
        return ChartSpec(
            chart_type="bubble",
            x_field=numeric_cols[0],
            y_fields=[numeric_cols[1], numeric_cols[2]],
            series_field=None,
            title=f"{numeric_cols[1]} vs {numeric_cols[0]} (size: {numeric_cols[2]})",
            confidence=0.7,
        )

    return None


from app.llm.base import LLMProvider
from app.pipeline.llm_utils import parse_llm_json


LLM_CONFIDENCE_THRESHOLD = 0.7

LLM_PROMPT = """You are a Splunk visualization expert. Given an SPL query and the first rows of its result, pick the best chart type and axes.

Output ONLY a JSON object with these exact fields:
{
    "chart_type": "line|bar|column|stacked_bar|pie|area|scatter|bubble|single_value|gauge|heatmap",
    "x_field": "<column name or null>",
    "y_fields": ["<column name>", ...],
    "series_field": "<column name or null>",
    "title": "<short title>",
    "confidence": <0.0-1.0>
}

Do NOT wrap in markdown fences. Do NOT add commentary."""


class ChartInferer:
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def infer(self, spl: str, rows: list[dict]) -> ChartSpec | None:
        guess = infer_heuristic(spl, rows)
        if guess is None:
            return None
        if guess.confidence > LLM_CONFIDENCE_THRESHOLD:
            return guess
        return await self._refine_with_llm(spl, rows, guess)

    async def _refine_with_llm(
        self, spl: str, rows: list[dict], guess: ChartSpec
    ) -> ChartSpec:
        sample_rows = rows[:5]
        user_prompt = (
            f"SPL: {spl}\n\n"
            f"Sample rows: {sample_rows}\n\n"
            f"Heuristic guess: {guess.model_dump_json()}\n\n"
            f"Return the best ChartSpec JSON for these results."
        )
        try:
            raw = await self._llm.generate(
                system_prompt=LLM_PROMPT,
                history=[],
                user_prompt=user_prompt,
            )
            parsed = parse_llm_json(raw, fallback=None)
            if parsed is None or not isinstance(parsed, dict):
                return guess
            # Preserve truncation flags from heuristic
            parsed.setdefault("truncated", guess.truncated)
            parsed.setdefault("truncation_note", guess.truncation_note)
            return ChartSpec(**parsed)
        except Exception:
            return guess
