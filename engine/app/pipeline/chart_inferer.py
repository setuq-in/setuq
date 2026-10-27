import re

from app.api.schemas import ChartSpec
from app.llm.base import LLMProvider
from app.pipeline.llm_utils import parse_llm_json

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


# Explicit chart-type requests in the user's natural-language query. Ordered:
# more specific phrases first (e.g. "stacked" before "bar"). Each maps to a
# canonical ChartSpec.chart_type.
_REQUESTED_CHART_TYPES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(pie|donut|doughnut)\b", re.IGNORECASE), "pie"),
    (re.compile(r"\bstacked\b", re.IGNORECASE), "stacked_bar"),
    (re.compile(r"\bcolumn\b", re.IGNORECASE), "column"),
    (re.compile(r"\bbar\b", re.IGNORECASE), "bar"),
    (re.compile(r"\barea\b", re.IGNORECASE), "area"),
    (re.compile(r"\bline\b", re.IGNORECASE), "line"),
    (re.compile(r"\bbubble\b", re.IGNORECASE), "bubble"),
    (re.compile(r"\bscatter\b", re.IGNORECASE), "scatter"),
    (re.compile(r"\bheat\s?map\b", re.IGNORECASE), "heatmap"),
    (re.compile(r"\bgauge\b", re.IGNORECASE), "gauge"),
]


def detect_requested_chart_type(query: str | None) -> str | None:
    """Return the FIRST chart type the user explicitly asked for, else None.

    Patterns are ordered most-specific-first so 'stacked bar' resolves to
    'stacked_bar' rather than 'bar'.
    """
    if not query:
        return None
    for pattern, chart_type in _REQUESTED_CHART_TYPES:
        if pattern.search(query):
            return chart_type
    return None


# Splits a query into chart requests: "pie and bar", "pie, line, bar",
# "pie & column", "bar / line". Each segment yields at most one chart type,
# which also avoids "stacked bar" double-counting as stacked_bar + bar.
_CHART_SPLIT = re.compile(r"[,/&]|\b(?:and|plus|as well as)\b", re.IGNORECASE)


def detect_requested_chart_types(query: str | None) -> list[str]:
    """Return every distinct chart type the user asked for, in query order.

    One type per segment (segments split on and/comma/&//). Empty when no
    specific type is named (generic 'chart' words are handled by wants_chart).
    """
    if not query:
        return []
    types: list[str] = []
    for segment in _CHART_SPLIT.split(query):
        t = detect_requested_chart_type(segment)
        if t and t not in types:
            types.append(t)
    if not types:
        t = detect_requested_chart_type(query)
        if t:
            types.append(t)
    return types


# Generic chart-intent words (no specific type named). Charts are opt-in: we
# only build one when the user explicitly asks for a visualization — either by
# naming a type ("pie", "bar" -> _REQUESTED_CHART_TYPES) or with a generic verb
# here. A plain data question ("top 10 products") never auto-charts.
_CHART_INTENT_PATTERN = re.compile(
    r"\b(chart|graph|plot|visuali[sz]e|visuali[sz]ation|diagram|histogram)\b",
    re.IGNORECASE,
)


def wants_chart(query: str | None) -> bool:
    """True when the query explicitly asks for a chart (generic verb or a
    named chart type). Gates all chart inference — no request, no chart."""
    if not query:
        return False
    if _CHART_INTENT_PATTERN.search(query):
        return True
    return detect_requested_chart_type(query) is not None


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

    @staticmethod
    def _spec_for_type(base: ChartSpec, chart_type: str) -> ChartSpec:
        """Clone the heuristic's axes/fields but force a user-named chart type."""
        return base.model_copy(update={
            "chart_type": chart_type,
            "confidence": 1.0,
            "requested_by_user": True,
        })

    async def infer_all(
        self, spl: str, rows: list[dict], query: str | None = None
    ) -> list[ChartSpec]:
        """Return one ChartSpec per chart the user asked for.

        - No chart request -> [] (charts are opt-in).
        - N named types ("pie and bar") -> N specs (UI shows a dropdown).
        - Generic "chart"/"plot" with no type -> single best-fit spec.
        """
        if not wants_chart(query):
            return []
        guess = infer_heuristic(spl, rows)
        if guess is None:
            return []
        requested = detect_requested_chart_types(query)
        if requested:
            # User named one or more types — honor each over the heuristic,
            # reusing the inferred axes/fields. Skip the LLM refine step.
            return [self._spec_for_type(guess, t) for t in requested]
        # Generic request, no explicit type — one best-fit chart.
        guess.requested_by_user = True
        if guess.confidence > LLM_CONFIDENCE_THRESHOLD:
            return [guess]
        return [await self._refine_with_llm(spl, rows, guess)]

    async def infer(
        self, spl: str, rows: list[dict], query: str | None = None
    ) -> ChartSpec | None:
        """Backward-compatible single-chart entry point (first requested)."""
        specs = await self.infer_all(spl, rows, query)
        return specs[0] if specs else None

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
            response = await self._llm.generate(
                system_prompt=LLM_PROMPT,
                history=[],
                user_prompt=user_prompt,
            )
            parsed = parse_llm_json(response.content, fallback=None)
            if parsed is None or not isinstance(parsed, dict):
                return guess
            # Preserve truncation flags from heuristic
            parsed.setdefault("truncated", guess.truncated)
            parsed.setdefault("truncation_note", guess.truncation_note)
            return ChartSpec(**parsed)
        except Exception:
            return guess
