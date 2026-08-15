import fnmatch
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from opentelemetry import metrics as otel_metrics

_logger = logging.getLogger("setuq.guardrails")
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


class GuardrailConfigError(ValueError):
    """Raised when the guardrail YAML is missing, malformed, or incomplete."""


@dataclass
class GuardrailResult:
    passed: bool
    violations: list[str]


# Default max time range in days (overridable per-instance)
MAX_TIME_RANGE_DAYS = 30


def load_guardrail_config(path: str) -> dict:
    """Load guardrail rules from a YAML file — the sole source of truth.

    There are no code defaults. Both keys are REQUIRED:

    - ``max_time_range_days`` (positive int)
    - ``resource_heavy_patterns``: list of ``{pattern, reason}`` maps. May be an
      empty list to disable resource-heavy blocking. A pattern that fails to
      compile is skipped (logged), never fatal.

    Raises ``GuardrailConfigError`` on a missing/malformed file or a missing/
    invalid required key. Returns
    ``{"max_time_range_days": int, "resource_heavy_patterns": list}``.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise GuardrailConfigError(f"Guardrail config not found: {path}")
    try:
        content = yaml.safe_load(file_path.read_text())
    except yaml.YAMLError as exc:
        raise GuardrailConfigError(f"Guardrail config YAML malformed ({path}): {exc}") from exc
    if not isinstance(content, dict):
        raise GuardrailConfigError(f"Guardrail config must be a mapping: {path}")

    max_days = content.get("max_time_range_days")
    if not isinstance(max_days, int) or isinstance(max_days, bool) or max_days <= 0:
        raise GuardrailConfigError("max_time_range_days is required and must be a positive integer")

    raw_patterns = content.get("resource_heavy_patterns")
    if not isinstance(raw_patterns, list):
        raise GuardrailConfigError("resource_heavy_patterns is required and must be a list")
    patterns: list[tuple[str, str]] = []
    for item in raw_patterns:
        if not isinstance(item, dict):
            continue
        pattern = item.get("pattern")
        reason = item.get("reason", "blocked by guardrail rule")
        if not isinstance(pattern, str):
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            _logger.warning("Skipping invalid guardrail pattern %r: %s", pattern, exc)
            continue
        patterns.append((pattern, str(reason)))
    return {"max_time_range_days": max_days, "resource_heavy_patterns": patterns}


class QueryGuardrail:
    def __init__(
        self,
        known_indexes: list[str] | None,
        max_time_range_days: int,
        resource_heavy_patterns: list[tuple[str, str]],
    ):
        self._known_indexes = set(known_indexes or [])
        self._max_time_range_days = max_time_range_days
        self._resource_heavy_patterns = resource_heavy_patterns

    def update_known_indexes(self, indexes: list[str]) -> None:
        self._known_indexes = set(indexes)

    def validate(self, spl: str) -> GuardrailResult:
        """Validate SPL against all guardrails. Raises GuardrailViolation if any fail."""
        violations: list[str] = []

        violations.extend(self._check_index(spl))
        violations.extend(self._check_time_range(spl))
        violations.extend(self._check_resource_heavy(spl))
        violations.extend(self._check_join_max(spl))

        result = GuardrailResult(passed=len(violations) == 0, violations=violations)
        if not result.passed:
            for v in violations:
                vl = v.lower()
                if "earliest" in vl or "latest" in vl or "time range" in vl:
                    rule = "time_range"
                elif "index" in vl:
                    rule = "index"
                else:
                    rule = "resource_heavy"
                _violation_counter.add(1, {"rule": rule})
            raise GuardrailViolation("; ".join(result.violations))
        return result

    def _validate_index_token(self, raw: str) -> str | None:
        idx_clean = raw.strip('"').strip("'").strip()
        if not idx_clean:
            return None
        if idx_clean == "*":
            return "Wildcard index '*' too broad — specify an index"
        if "*" in idx_clean:
            # Partial wildcard (e.g. sec*) is allowed only if it actually
            # resolves against the known schema — an arbitrary glob that
            # matches nothing is not a legitimate narrowing, it's a probe.
            if self._known_indexes and not any(
                fnmatch.fnmatchcase(k, idx_clean) for k in self._known_indexes
            ):
                return f"Wildcard index '{idx_clean}' matches no known index"
            return None
        if idx_clean not in self._known_indexes:
            return f"Unknown index '{idx_clean}' — not in schema"
        return None

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
            # Stop at subsearch/paren boundaries so `[search index=x]` yields "x".
            for match in re.findall(r"index\s*=\s*([^\s\]\)]+)", stripped):
                v = self._validate_index_token(match)
                if v:
                    violations.append(v)
            # `index IN (a, b, c)` bypasses the index= regex above entirely.
            for group in re.findall(r"index\s+in\s*\(([^)]*)\)", stripped, re.IGNORECASE):
                for token in group.split(","):
                    v = self._validate_index_token(token)
                    if v:
                        violations.append(v)
        return violations

    @staticmethod
    def _find_unquoted_values(spl: str, keyword: str) -> list[str]:
        """Raw values for `keyword=...` occurrences that start outside any
        quoted string, so e.g. `eval note="earliest=-1d"` can't spoof the
        time-range check — and every occurrence is returned, not just the
        first, so a bound in a subsearch can't hide an unbound outer range."""
        values: list[str] = []
        in_squote = in_dquote = False
        kw_re = re.compile(re.escape(keyword) + r"\s*=\s*", re.IGNORECASE)
        i, n = 0, len(spl)
        while i < n:
            ch = spl[i]
            if ch == "'" and not in_dquote:
                in_squote = not in_squote
                i += 1
                continue
            if ch == '"' and not in_squote:
                in_dquote = not in_dquote
                i += 1
                continue
            if not in_squote and not in_dquote:
                m = kw_re.match(spl, i)
                if m:
                    vm = re.match(r'"([^"]*)"|\'([^\']*)\'|([^\s|\]\)]+)', spl[m.end():])
                    if vm:
                        values.append(next(g for g in vm.groups() if g is not None))
                        i = m.end() + vm.end()
                        continue
            i += 1
        return values

    def _validate_time_value(self, val: str, field: str) -> str | None:
        val = val.strip("\"'")

        if field == "latest" and val.lower() == "now":
            return None

        # Block all-time / epoch zero
        if val in ("0", "1", "epoch"):
            return f"{field}={val} not allowed — use a relative time range"

        # Block snap-relative like @d-30d (too complex to bound safely)
        if "@" in val:
            return f"Snap-relative time ({field} @) not allowed — use -Nd or -Nh format"

        # Check days: -Nd
        m_days = re.match(r"^-?(\d+)d$", val, re.IGNORECASE)
        if m_days:
            days = int(m_days.group(1))
            if days > self._max_time_range_days:
                return f"{field} range {days}d exceeds max {self._max_time_range_days}d"
            return None

        # Check hours: -Nh
        m_hours = re.match(r"^-?(\d+)h$", val, re.IGNORECASE)
        if m_hours:
            hours = int(m_hours.group(1))
            max_hours = self._max_time_range_days * 24
            if hours > max_hours:
                return f"{field} range {hours}h exceeds max {max_hours}h"
            return None

        # Months/weeks/quarters are disallowed; minutes/seconds are allowed but
        # must still be bounded by the same max window (so -999999m can't slip by).
        m_other = re.match(r"^-?(\d+)(mon|w|m|s|q)$", val, re.IGNORECASE)
        if m_other:
            amount = int(m_other.group(1))
            unit = m_other.group(2).lower()
            if unit in ("mon", "w", "q"):
                return f"Time unit '{unit}' not allowed — use -Nd or -Nh"
            minutes = amount if unit == "m" else amount / 60.0  # 'm' minutes, 's' seconds
            max_minutes = self._max_time_range_days * 24 * 60
            if minutes > max_minutes:
                return f"{field} range {val} exceeds max {self._max_time_range_days}d"
            return None

        # Unknown format — reject for safety
        return f"Unrecognized {field}= format '{val}' — use -Nd or -Nh"

    def _check_time_range(self, spl: str) -> list[str]:
        earliest_values = self._find_unquoted_values(spl, "earliest")
        if not earliest_values:
            return ["No time range specified — add earliest= to bound the query"]

        violations = [
            v for val in earliest_values
            if (v := self._validate_time_value(val, "earliest")) is not None
        ]
        for val in self._find_unquoted_values(spl, "latest"):
            v = self._validate_time_value(val, "latest")
            if v is not None:
                violations.append(v)
        return violations

    def _check_resource_heavy(self, spl: str) -> list[str]:
        violations = []
        for pattern, reason in self._resource_heavy_patterns:
            if re.search(pattern, spl, re.IGNORECASE | re.DOTALL):
                violations.append(reason)
        return violations

    _JOIN_MAX_LIMIT = 50_000
    _JOIN_MAX_RE = re.compile(r"\|\s*join\b[^|]*\bmax\s*=\s*(\d+)", re.IGNORECASE)

    def _check_join_max(self, spl: str) -> list[str]:
        """The resource-heavy `join` pattern only checks that `max=` is
        *present* — a hallucinated `max=999999999` satisfies it while still
        being unbounded in practice. Bound the value itself."""
        violations = []
        for m in self._JOIN_MAX_RE.finditer(spl):
            n = int(m.group(1))
            if n > self._JOIN_MAX_LIMIT:
                violations.append(f"join max={n} exceeds safe bound ({self._JOIN_MAX_LIMIT})")
        return violations
