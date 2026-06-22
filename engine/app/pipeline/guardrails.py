import re
from dataclasses import dataclass
from opentelemetry import metrics as otel_metrics
_meter = otel_metrics.get_meter("setuq.guardrails")
_violation_counter = _meter.create_counter(
    "guardrail.violations",
    description="Count of guardrail violations by rule type",
)


class GuardrailViolation(Exception):
    """Raised when generated SPL violates a guardrail."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class GuardrailResult:
    passed: bool
    violations: list[str]


# Patterns considered resource-heavy without proper limits
_RESOURCE_HEAVY_PATTERNS = [
    (r"\|\s*join\b(?![^|]*\b(?:max|overwrite)\s*=)", "Unbounded join — add max= or use stats instead"),
    (r"\|\s*collect\b(?!.*index=)", "collect without target index"),
    (r"\|\s*delete\b", "delete command not allowed"),
    (r"\|\s*outputlookup\b", "outputlookup not allowed via agent"),
]

# Default max time range in days (overridable per-instance)
MAX_TIME_RANGE_DAYS = 30


class QueryGuardrail:
    def __init__(self, known_indexes: list[str] | None = None, max_time_range_days: int = MAX_TIME_RANGE_DAYS):
        self._known_indexes = set(known_indexes or [])
        self._max_time_range_days = max_time_range_days

    def update_known_indexes(self, indexes: list[str]) -> None:
        self._known_indexes = set(indexes)

    def validate(self, spl: str) -> GuardrailResult:
        """Validate SPL against all guardrails. Raises GuardrailViolation if any fail."""
        violations: list[str] = []

        violations.extend(self._check_index(spl))
        violations.extend(self._check_time_range(spl))
        violations.extend(self._check_resource_heavy(spl))

        result = GuardrailResult(passed=len(violations) == 0, violations=violations)
        if not result.passed:
            for v in violations:
                if "time range" in v.lower() or "earliest" in v.lower():
                    rule = "time_range"
                elif "index" in v.lower():
                    rule = "index"
                else:
                    rule = "resource_heavy"
                _violation_counter.add(1, {"rule": rule})
            raise GuardrailViolation("; ".join(result.violations))
        return result

    def _check_index(self, spl: str) -> list[str]:
        if not self._known_indexes:
            return []
        # Strip quoted strings to avoid splitting on | inside strings
        spl_stripped = re.sub(r'"[^"]*"', '""', spl)
        spl_stripped = re.sub(r"'[^']*'", "''", spl_stripped)
        search_parts = re.split(r"\|", spl_stripped)
        violations = []
        for part in search_parts:
            stripped = part.strip()
            if re.match(r"(collect|outputlookup)\b", stripped, re.IGNORECASE):
                continue
            for match in re.findall(r"index\s*=\s*(\S+)", stripped):
                idx_clean = match.strip('"').strip("'")
                if not idx_clean:
                    continue
                if idx_clean not in self._known_indexes and "*" not in idx_clean:
                    violations.append(f"Unknown index '{idx_clean}' — not in schema")
        return violations

    def _check_time_range(self, spl: str) -> list[str]:
        has_earliest = bool(re.search(r"earliest\s*=", spl, re.IGNORECASE))
        if not has_earliest:
            return ["No time range specified — add earliest= to bound the query"]

        # Extract earliest value
        match = re.search(r"earliest\s*=\s*([^\s|]+)", spl, re.IGNORECASE)
        if not match:
            return []
        val = match.group(1).strip("\"'")

        # Block all-time / epoch zero
        if val in ("0", "1", "epoch"):
            return ["earliest=0 not allowed — use a relative time range"]

        # Block snap-relative like @d-30d (too complex to bound safely)
        if "@" in val:
            return ["Snap-relative time (@) not allowed — use -Nd or -Nh format"]

        # Check days: -Nd
        m_days = re.match(r"^-?(\d+)d$", val, re.IGNORECASE)
        if m_days:
            days = int(m_days.group(1))
            if days > self._max_time_range_days:
                return [f"Time range {days}d exceeds max {self._max_time_range_days}d"]
            return []

        # Check hours: -Nh
        m_hours = re.match(r"^-?(\d+)h$", val, re.IGNORECASE)
        if m_hours:
            hours = int(m_hours.group(1))
            max_hours = self._max_time_range_days * 24
            if hours > max_hours:
                return [f"Time range {hours}h exceeds max {max_hours}h"]
            return []

        # Block months/weeks/minutes unless very short (not implemented)
        m_other = re.match(r"^-?(\d+)(mon|w|m|s|q)$", val, re.IGNORECASE)
        if m_other:
            unit = m_other.group(2).lower()
            if unit in ("mon", "w", "q"):
                return [f"Time unit '{unit}' not allowed — use -Nd or -Nh"]
            return []  # minutes/seconds are fine

        # Unknown format — reject for safety
        return [f"Unrecognized earliest= format '{val}' — use -Nd or -Nh"]

    def _check_resource_heavy(self, spl: str) -> list[str]:
        violations = []
        for pattern, reason in _RESOURCE_HEAVY_PATTERNS:
            if re.search(pattern, spl, re.IGNORECASE | re.DOTALL):
                violations.append(reason)
        return violations
